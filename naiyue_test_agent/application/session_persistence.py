"""Critical persistence boundaries, separate from optional event consumers."""

import logging
import json
import asyncio
import unicodedata
from copy import deepcopy
from threading import Lock
from contextlib import nullcontext

from .session_storage import SessionPersistenceError
from .execution_storage import root_from_row
from naiyue_test_agent.execution_roots import execution_error
from .session_recovery import (
    _classification, _identity_matches, _message_facts, classify_recovery,
    recovery_record, build_recovery_update, validate_saved_approval,
)
from .session_approval import is_pre_tool_approval
from langchain_core.messages import BaseMessage
from langchain_core.tools import ToolException
from langgraph.graph import END
from uuid import uuid4
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking
from .subagent_history import subagent_records
from .chat_history import collect_chat_updates


logger = logging.getLogger(__name__)


class SessionPersistence:
    def __init__(self, storage, sessions, *, catalog, tool_registry, permission_settings, usage_persistence=None, title_max_chars=40, checkpoint_settings=None, memory=None, memory_jobs=None):
        self.storage = storage
        self.sessions = sessions
        self._catalog, self._tool_registry = catalog, tool_registry
        self._permission_settings = permission_settings
        self._usage = usage_persistence
        self._title_max_chars = title_max_chars
        self._checkpoint_settings = checkpoint_settings
        self._lock = Lock()
        self._active = {}
        self._blocked = set()
        self._recovering = set()
        self._history_locks = {}
        self._prepared = {}
        self._memory_jobs = memory_jobs
        self._memory = memory
        self._memory_agent = None

    async def astatus(self, session_id):
        row = await run_blocking(self.storage.get, session_id)
        value = {key: row[key] for key in ('session_id', 'model_id', 'thinking_level', 'workspace_root',
            'state', 'updated_at', 'last_run_status', 'title', 'permission_mode', 'checkpoint_id', 'conversation_revision')}
        value.update(pinned=bool(row['pinned']), active_tools=json.loads(row['active_tool_names_json']),
            busy=False, run_id=None, manual_review_required=row['state'] == 'review_required', pending_approval=False)
        loaded = self.sessions.has_session(session_id)
        if loaded:
            value.update(self.sessions.require(session_id).describe_session())
        else:
            value.update(deepcopy(self._prepared.get(session_id, {'recovery': {'kind': 'unavailable',
                'reason': 'session_not_prepared', 'actions': [], 'unconfirmed_tool_call_ids': []}})))
        with self._lock:
            blocked = session_id in self._blocked
        value['history_ready'] = (session_id in self._prepared or loaded and row['run_id'] is None) and not blocked
        value['can_run'] = (loaded and not blocked and row['state'] != 'running' and not value['busy']
                            and not value['manual_review_required'] and not value['pending_approval'])
        root = root_from_row(row)
        value['project_id'] = row['project_id']
        value['execution_root_kind'] = row['execution_root_kind']
        value['execution_root_error'] = execution_error(root, row['project_id'])
        value['can_run'] = value['can_run'] and value['execution_root_error'] is None
        value.pop('approval_run_id', None)
        from .reply_storage import latest
        boundary = await run_blocking(latest, self.storage, session_id)
        value['last_reply_id'] = boundary['reply_id'] if boundary else None
        return value

    async def aquery_history(self, *, source_session_id, workspace_root, agent_path, request, settings):
        session = self.sessions.require(source_session_id)
        current = session.describe_session()
        if (not current['busy'] or current['workspace_root'] != str(workspace_root)
                or 'history' not in session.get_active_tools(agent_path)):
            raise ToolException('history_scope_mismatch: the calling session or role is unavailable.')
        from naiyue_test_agent.tools.permissions import require_execution_directory
        facts = session.snapshot_for_run()
        require_execution_directory(facts['execution_root'], facts['project_id'])
        try:
            return await run_blocking(self.storage.read_history_tool, source_session_id, current['project_id'], request, settings)
        except SessionPersistenceError as error:
            logger.warning('history_query_failed error_type=%s', type(error).__name__)
            raise ToolException(f'History query failed ({error}). Query the directory again if the source changed.') from error


    def bind_memory_agent(self, agent):
        self._memory_agent = agent

    async def aquery_memory(self, *, source_session_id, workspace_root, agent_path, request, tool_call_id=None):
        from .memory_access import MemoryReadError
        session = self.sessions.require(source_session_id)
        current = session.describe_session()
        if (not current['busy'] or current['workspace_root'] != str(workspace_root)
                or 'memory' not in session.get_active_tools(agent_path)):
            raise ToolException('memory_scope_mismatch: the calling session or role is unavailable.')
        from naiyue_test_agent.tools.permissions import require_execution_directory
        facts = session.snapshot_for_run()
        require_execution_directory(facts['execution_root'], facts['project_id'])
        if self._memory is None:
            raise ToolException('memory_unavailable: persistent memory is unavailable.')
        try:
            if request['action']=='refresh_overview':
                if self._memory_jobs is None:raise MemoryReadError('memory_unavailable')
                identity,model=session.resolve_model()
                return await self._memory_jobs.request(source_session_id=source_session_id,
                    target_session_id=request['target_session_id'] or source_session_id,workspace_root=workspace_root,
                    origin_run_id=current['run_id'],model_id=identity,model=model,tool_call_id=tool_call_id,
                    start_sequence=request['start_sequence'],end_sequence=request['end_sequence'])
            if request['action'] in ('record','update','disable','forget'):
                async with self._history_lock(source_session_id):
                    try:
                        updates = await collect_chat_updates(self._memory_agent,self.storage,source_session_id)
                        await run_blocking(self.storage.save_chat_updates,source_session_id,updates)
                    except Exception as error:
                        logger.error('memory_source_save_failed error_type=%s',type(error).__name__)
                        raise ToolException('memory_source_save_failed: memory was not written.') from error
                    return await run_blocking(self._memory.query,source_session_id,current['project_id'],request)
            return await run_blocking(self._memory.query, source_session_id, current['project_id'], request)
        except MemoryReadError as error:
            logger.warning('memory_query_failed error_type=%s', type(error).__name__)
            raise ToolException(f'Memory query failed ({error}).') from error

    async def prepare_memory(self,session_id,root,messages,model,*,preview=False):
        if self._memory_jobs is None:return []
        try:return await run_blocking(self._memory_jobs.prepare,session_id,root,messages,model,preview=preview)
        except (MemoryError,OSError,ValueError,SessionPersistenceError) as error:
            logger.warning('memory_context_prepare_failed error_type=%s',type(error).__name__)
            return []

    def _history_lock(self, session_id):
        return self._history_locks.setdefault(session_id, asyncio.Lock())

    async def aprepare(self, session_id, agent):
        async with self._history_lock(session_id):
            try:
                row = await run_blocking(self.storage.get, session_id)
                if not self.sessions.has_session(session_id) and row['state'] == 'ready':
                    try:
                        await self.arestore(session_id, agent)
                    except (SessionPersistenceError, ValueError):
                        logger.warning('session_prepare_restore_unavailable')
                snapshot = await agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
                if not self.sessions.has_session(session_id):
                    saved = await self._read_history(session_id, agent, snapshot=snapshot, include_messages=False)
                    saved.pop('messages', None)
                else:
                    saved = {}
                updates = await collect_chat_updates(agent, self.storage, session_id, snapshot=snapshot)
                await run_blocking(self.storage.save_chat_updates, session_id, updates)
                self._prepared[session_id] = saved
                await self._ensure_title(session_id)
            except Exception as error:
                self._prepared.pop(session_id, None)
                logger.error('chat_history_prepare_failed error_type=%s', type(error).__name__)
                if isinstance(error, SessionPersistenceError):
                    raise
                raise SessionPersistenceError('chat_history_unavailable') from error

    def start(self, session_id, run_id):
        with self._lock:
            if session_id in self._active or session_id in self._recovering or session_id in self._blocked:
                raise SessionPersistenceError('session_recovery_blocked')
            self._active[session_id] = run_id
        # Reserve this owner before IO, without blocking event-loop readers of
        # the coordination lock while SQLite waits for another writer.
        try:
            self.storage.mark_running(session_id, run_id)
        except BaseException:
            with self._lock:
                if self._active.get(session_id) == run_id:
                    self._active.pop(session_id)
            raise

    async def adelete(self, session_id, agent):
        if self._history_lock(session_id).locked():
            raise SessionPersistenceError('session_recovery_blocked')
        try:
            memory_guard=self._memory_jobs.deletion_guard(session_id) if self._memory_jobs else nullcontext()
            with self.sessions.background_tasks.deletion_guard(session_id),memory_guard:
                async with self._history_lock(session_id):
                    result = await self._delete(session_id, agent)
                    self.sessions.background_tasks.forget_session(session_id)
                    if self._memory_jobs:self._memory_jobs.forget_session(session_id)
                    return result
        except ToolException as error:
            raise SessionPersistenceError('session_background_tasks_active') from error

    async def _delete(self, session_id, agent):
        with self._lock, self.sessions.deletion_guard(session_id):
            if session_id in self._active or session_id in self._recovering:
                raise SessionPersistenceError('session_recovery_blocked')
            self._recovering.add(session_id)
        try:
            row = await run_blocking(self.storage.get, session_id)
            children = set()
            async for snapshot in agent.aget_state_history({'configurable': {'thread_id': row['thread_id']}}):
                children.update(f'subagent:{item["agent_id"]}' for item in subagent_records(session_id, snapshot.values.get('messages', [])))
            children.update(self.sessions.child_thread_ids(session_id))
            children.update(item['thread_id'] for item in await run_blocking(self.storage.chat_heads, session_id)
                            if item['thread_id'] != row['thread_id'])
            if self.sessions.mcp_resources is not None:
                await self.sessions.mcp_resources.release_session(session_id)
            await run_blocking(self.storage.delete, session_id, children)
            with self._lock:
                self.sessions.remove_session(session_id)
                self._blocked.discard(session_id)
                self._prepared.pop(session_id, None)
        except Exception as error:
            logger.error('session_delete_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_delete_unconfirmed') from error
        finally:
            with self._lock:
                self._recovering.discard(session_id)


    async def afinish(self, session_id, run_id, agent, observation, session):
        async with self._history_lock(session_id):
            with self._lock:
                if self._active.get(session_id) != run_id:
                    return
            if self._usage is not None:
                try:
                    await self._usage.aflush_run(session_id, run_id)
                except Exception as error:
                    logger.error('model_usage_finalize_failed error_type=%s', type(error).__name__)
            try:
                status = observation.status
                state = ('review_required' if session['manual_review_required'] else
                         'interrupted' if status == 'interrupted' else 'failed' if status == 'failed' else 'ready')
                previous = await run_blocking(self.storage.get, session_id)
                checkpoint_id, namespace = previous['checkpoint_id'], previous['checkpoint_ns']
                snapshot = None
                if state == 'ready':
                    snapshot = await await_settled(agent.aget_state({'configurable': {'thread_id': session['thread_id']}}))
                    checkpoint_id = snapshot.config.get('configurable', {}).get('checkpoint_id')
                    namespace = snapshot.config.get('configurable', {}).get('checkpoint_ns', '')
                    if (not checkpoint_id or snapshot.next or
                            any(task.interrupts or task.error for task in snapshot.tasks)):
                        raise SessionPersistenceError('checkpoint_not_safely_finished')
                updates = await await_settled(collect_chat_updates(agent, self.storage, session_id, snapshot=snapshot))
                await run_blocking(self.storage.finish_run, session_id, state, checkpoint_id, namespace, status, updates,
                                  subagent_run_id=run_id)
                self._prepared[session_id] = {}
                await self._ensure_title(session_id)
            except Exception as error:
                with self._lock:
                    self._blocked.add(session_id)
                if self._memory_jobs is not None:
                    try:await self._memory_jobs.after_finish(session_id,run_id,'failed',model_id=self._catalog.default_model_id)
                    except Exception as secondary:logger.error('memory_cancel_secondary_failed error_type=%s',type(secondary).__name__)
                logger.error('session_persistence_finalize_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('session_save_unconfirmed') from error
            else:
                if self._memory_jobs is not None:
                    try:
                        identity,model=self.sessions.require(session_id).resolve_model()
                        await self._memory_jobs.after_finish(session_id,run_id,status if observation.mode!='compact' else 'skipped',model_id=identity,model=model)
                    except Exception as error:logger.error('memory_after_finish_failed error_type=%s',type(error).__name__)
                if state == 'ready' and self._checkpoint_settings is not None:
                    try:
                        from .checkpoint_retention import prune_after_finish
                        await await_settled(prune_after_finish(agent, self.storage, session_id, run_id,
                            self._checkpoint_settings, snapshot=snapshot))
                    except Exception as error:
                        logger.error('checkpoint_retention_failed error_type=%s', type(error).__name__)
            finally:
                with self._lock:
                    self._active.pop(session_id, None)


    async def _ensure_title(self, session_id):
        try:
            row = await run_blocking(self.storage.get, session_id)
            if row['title'] is not None:
                return
            records = await run_blocking(self.storage._read, """SELECT body_json FROM naiyue_chat_messages
                WHERE session_id=? AND thread_id=? AND message_type='human' AND kind='message'
                ORDER BY sequence LIMIT 1""", (session_id, row['thread_id']))
            if records:
                from naiyue_test_agent.model_access.messages import content_text
                content = content_text(json.loads(records[0]['body_json'])['data']['content'])
                content = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in content)
                title = ' '.join(content.split())[:self._title_max_chars].strip()
                if title:
                    await run_blocking(self.storage.ensure_title, session_id, title)
        except Exception as error:
            logger.warning('session_title_save_failed error_type=%s', type(error).__name__)

    async def ahistory(self, session_id, agent):
        return await self._read_history(session_id, agent)


    async def arecover(self, session_id, agent, *, checkpoint_id, action):
        async with self._history_lock(session_id):
            with self._lock:
                if session_id in self._active or session_id in self._recovering or session_id in self._blocked:
                    raise SessionPersistenceError('session_recovery_blocked')
                self._recovering.add(session_id)
            try:
                if self.sessions.has_session(session_id):
                    raise SessionPersistenceError('session_recovery_blocked')
                return await self._recover_checkpoint(session_id, agent, checkpoint_id, action)
            finally:
                with self._lock:
                    self._recovering.discard(session_id)


    async def arestore(self, session_id, agent):
        with self._lock:
            if session_id in self._active or session_id in self._recovering or session_id in self._blocked:
                raise SessionPersistenceError('session_recovery_blocked')
            self._recovering.add(session_id)
        try:
            row = await run_blocking(self.storage.get, session_id)
            if row['state'] != 'ready':
                raise SessionPersistenceError('session_recovery_blocked')
            root, names = await self._aprepare_saved_configuration(row)
            snapshot = await await_settled(agent.aget_state({'configurable': {'thread_id': row['thread_id']}}))
            actual = snapshot.config.get('configurable', {}).get('checkpoint_id')
            namespace = snapshot.config.get('configurable', {}).get('checkpoint_ns', '')
            if (row['checkpoint_id'] != actual or row['checkpoint_ns'] != namespace or snapshot.next
                    or any(task.interrupts or task.error for task in snapshot.tasks)):
                raise SessionPersistenceError('checkpoint_mismatch')
            self._install_session(row, root, names, recovering=True)
            return session_id
        finally:
            with self._lock:
                self._recovering.discard(session_id)


    async def _aprepare_saved_configuration(self, row):
        try:
            self._permission_settings.validate_mode(row['permission_mode'])
        except ValueError as error:
            raise SessionPersistenceError('invalid_permission_mode') from error
        saved = row['active_tool_names_json']
        try:
            names = json.loads(saved)
            from naiyue_test_agent.tools.subagent import subagent_selection
            names = subagent_selection(names)
            specs = self._tool_registry.select(names)
            names = tuple(spec.tool.name for spec in specs)
        except (ValueError, TypeError) as error:
            raise SessionPersistenceError('invalid_active_tools') from error
        self._catalog.with_thinking(row['model_id'], row['thinking_level'])
        # Saved identity can be installed for history even when execution is unavailable.
        root = root_from_row(row)
        return root.path if root is not None else None, names

    def _install_session(self, row, root, names, *, recovering=False, pending=None):
        session_id = row['session_id']
        with self._lock:
            if (session_id in self._active or session_id in self._blocked or
                    session_id in self._recovering and not recovering):
                raise SessionPersistenceError('session_recovery_blocked')
            self.sessions.install_restored_session(session_id, thread_id=row['thread_id'],
                model_id=row['model_id'], thinking_level=row['thinking_level'], workspace_root=root,
                active_tool_names=names, permission_mode=row['permission_mode'], pending=pending, conversation_revision=row['conversation_revision'],
                project_id=row['project_id'], execution_root_kind=row['execution_root_kind'])

    async def _read_history(self, session_id, agent, *, snapshot=None, include_messages=True):
        row = await run_blocking(self.storage.get, session_id)
        value = {key: row[key] for key in ('session_id', 'state', 'model_id', 'thinking_level', 'workspace_root', 'permission_mode')}
        value.update(messages=[], active_tools=[], checkpoint_id=None, checkpoint_ns='',
                     confirmed_checkpoint_id=row['checkpoint_id'], confirmed_checkpoint_ns=row['checkpoint_ns'])
        config = {'configurable': {'thread_id': row['thread_id']}}
        try:
            if snapshot is None:
                snapshot = await await_settled(agent.aget_state(config))
        except Exception as error:
            logger.error('session_history_read_failed error_type=%s', type(error).__name__)
            value['recovery'] = _classification('unavailable', 'checkpoint_unreadable')
            return value
        recovery = classify_recovery(row, snapshot)
        if _identity_matches(row, snapshot):
            value['checkpoint_id'] = snapshot.config.get('configurable', {}).get('checkpoint_id')
            messages = snapshot.values.get('messages', []) if isinstance(snapshot.values, dict) else None
            if include_messages and isinstance(messages, list) and all(isinstance(m, BaseMessage) for m in messages):
                value['messages'] = messages
        unavailable = None
        try:
            _, names = await self._aprepare_saved_configuration(row)
            value['active_tools'] = list(names)
            value['execution_root_error'] = execution_error(root_from_row(row), row['project_id'])
            if value['execution_root_error'] is not None:
                unavailable = 'execution_root_unavailable'
        except (ValueError, TypeError, ModelConfigError, SessionPersistenceError) as error:
            unavailable = 'saved_configuration_unavailable'
            logger.warning('session_history_configuration_unavailable error_type=%s', type(error).__name__)
        if self.sessions.has_session(session_id):
            unavailable = 'session_current_process'
        with self._lock:
            if (session_id in self._blocked or session_id in self._recovering
                    or session_id in self._active):
                unavailable = 'session_current_process'
        if unavailable:
            recovery = _classification('unavailable', unavailable, recovery['unconfirmed_tool_call_ids'])
        if recovery['kind'] == 'unavailable':
            logger.warning('session_history_unavailable reason=%s', recovery['reason'])
        elif recovery['kind'] in ('interrupted_execution', 'pending_approval') and value['checkpoint_id']:
            if (recovery['kind'] == 'pending_approval' and any(
                    'mcp' in call.get('subject', {}) for call in snapshot.values['tool_execution']['calls'])):
                recovery.update(reason='mcp_approval_requires_new_call', actions=['continue'])
            else:
                recovery['actions'] = (['restore_approval', 'continue'] if recovery['kind'] == 'pending_approval'
                                       else ['continue'])
        value['recovery'] = recovery
        return value


    async def _recover_checkpoint(self, session_id, agent, checkpoint_id, action):
        if action not in ('continue', 'restore_approval'):
            raise SessionPersistenceError('invalid_recovery_action')
        row = await run_blocking(self.storage.get, session_id)
        root, names = await self._aprepare_saved_configuration(row)
        binding = await run_blocking(self.storage.get_workspace_binding)
        if binding and str(root) != binding['root_path']:
            raise SessionPersistenceError('workspace_mismatch')
        config = {'configurable': {'thread_id': row['thread_id']}}
        try:
            snapshot = await await_settled(agent.aget_state(config))
        except Exception as error:
            logger.error('session_recovery_read_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('checkpoint_unreadable') from error
        if not _identity_matches(row, snapshot):
            raise SessionPersistenceError('checkpoint_mismatch')
        actual = snapshot.config['configurable'].get('checkpoint_id')
        if not actual or checkpoint_id != actual:
            raise SessionPersistenceError('checkpoint_mismatch')
        classification = classify_recovery(row, snapshot)
        if classification['kind'] not in ('interrupted_execution', 'pending_approval'):
            raise SessionPersistenceError('session_recovery_blocked')
        if action == 'restore_approval':
            if classification['kind'] != 'pending_approval':
                raise SessionPersistenceError('session_recovery_blocked')
            pending = await run_blocking(validate_saved_approval, self._permission_settings, self._tool_registry, row, snapshot, names)
            try:
                updates = await await_settled(collect_chat_updates(agent, self.storage, session_id, snapshot=snapshot))
                await run_blocking(self.storage.finish_run, session_id, 'interrupted', actual, '', 'interrupted', updates)
                self._install_session(row, root, names, recovering=True, pending=pending)
            except Exception as error:
                logger.error('session_approval_restore_save_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('session_save_unconfirmed') from error
            return {'session_id': session_id, 'checkpoint_id': actual, 'checkpoint_ns': '', 'state': 'interrupted'}
        record = recovery_record(row, snapshot)
        if record is not None:
            source_config = {'configurable': {'thread_id': row['thread_id'], 'checkpoint_ns': '',
                                             'checkpoint_id': record['source_checkpoint_id']}}
            try:
                source = await await_settled(agent.aget_state(source_config))
            except Exception as error:
                logger.error('session_recovery_source_read_failed error_type=%s', type(error).__name__)
                raise SessionPersistenceError('checkpoint_unreadable') from error
            if (not _identity_matches(row, source) or not source.created_at
                    or source.config['configurable'].get('checkpoint_id') != record['source_checkpoint_id']
                    or (source.metadata or {}).get('naiyue_recovery') is not None
                    or record['pre_tool_approval'] and not is_pre_tool_approval(source, source.values.get('messages', []))):
                raise SessionPersistenceError('invalid_recovery_record')
        record = record or {
            'version': 1, 'recovery_id': uuid4().hex, 'session_id': session_id, 'thread_id': row['thread_id'],
            'source_checkpoint_id': actual, 'source_namespace': '', 'original_state': row['state'],
            'pre_tool_approval': classification['kind'] == 'pending_approval', 'phase': None,
        }
        try:
            updates = await await_settled(collect_chat_updates(agent, self.storage, session_id, snapshot=snapshot))
            phases = ('cleared', 'paired', 'finished')
            start = phases.index(record['phase']) + 1 if record['phase'] else 0
            for phase in phases[start:]:
                values = build_recovery_update(snapshot, record) if phase == 'paired' else None
                node = 'tools' if phase == 'paired' else END
                record = {**record, 'phase': phase}
                marked = {**snapshot.config, 'metadata': {'naiyue_recovery': json.dumps(record, sort_keys=True)}}
                updated = await await_settled(agent.aupdate_state(marked, values, as_node=node))
                snapshot = await await_settled(agent.aget_state(config))
                if (not _identity_matches(row, snapshot) or snapshot.config != updated
                        or recovery_record(row, snapshot) != record):
                    raise SessionPersistenceError('session_save_unconfirmed')
            missing, invalid = _message_facts(snapshot.values.get('messages', []))
            if (snapshot.next or snapshot.tasks or missing or invalid or snapshot.values.get('tool_execution') != {}
                    or snapshot.values.get('run_stopped') is not False):
                raise SessionPersistenceError('session_save_unconfirmed')
            actual = snapshot.config['configurable']['checkpoint_id']
            updates = await await_settled(collect_chat_updates(agent, self.storage, session_id, snapshot=snapshot, previous=updates))
            await run_blocking(self.storage.finish_run, session_id, 'ready', actual, '', 'recovered', updates)
            self._install_session(row, root, names, recovering=True)
        except Exception as error:
            logger.error('session_recovery_save_failed error_type=%s', type(error).__name__)
            raise SessionPersistenceError('session_save_unconfirmed') from error
        return {'session_id': session_id, 'checkpoint_id': actual, 'checkpoint_ns': '',
                'recovery_id': record['recovery_id'], 'state': 'ready'}
