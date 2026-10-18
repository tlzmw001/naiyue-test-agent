"""Workspace-scoped public application operations for the HTTP adapter."""

import logging

from .errors import WebError, public_error
from .serialization import encoded_size
from .chat_history import history_page, history_window
from naiyue_test_agent.agent_runtime.async_utils import await_settled


logger = logging.getLogger(__name__)


class WebService:
    def __init__(self, application, delivery, settings):
        self.application, self.delivery, self.settings = application, delivery, settings

    def models(self):
        return {'default_model_id': self.application.default_model_id,
                'model_ids': list(self.application.model_ids), 'thinking_options': self.application.get_thinking_options()}

    def service_info(self):
        root = self.settings.workspace_root
        return {'workspace_root': str(root) if root is not None else None,
                'workspace_label': (root.name or root.anchor) if root is not None else None}

    async def skills(self):
        try:
            catalog = await self.application.aget_skills(self.settings.workspace_root)
            result = {'entries': [
                {'source_id': item.source_id, 'name': item.name, 'description': item.description,
                 'command': catalog.command_for(item)} for item in catalog.entries],
                'default_source': catalog.default_source,
                'diagnostics': [{key: row[key] for key in ('source_id', 'reason')} for row in catalog.diagnostics],
                'read_registered': any(spec.tool.name == 'read' for spec in self.application.get_registered_tools())}
        except Exception as error:
            logger.error('skill_catalog_read_failed error_type=%s', type(error).__name__)
            raise WebError('skill_catalog_unavailable', 503) from None
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def select_thinking(self, session_id, model_id, level):
        value = await self._session_status(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        await self._apply_setting(self.application.aselect_thinking(session_id, model_id, level),
            invalid_code='thinking_unavailable', invalid_event='web_thinking_selection_invalid',
            failure_event='web_thinking_selection_failed')
        return {'model_id': model_id, 'thinking_level': level}

    async def regenerate(self, session_id, body):
        value = await self._session_status(session_id)
        saved = await self.application.aget_regeneration_action(session_id, body.request_id)
        if saved:
            if (saved['reply_id'], saved['revision']) != (body.expected_reply_id, body.expected_conversation_revision):
                raise WebError('request_id_conflict')
            if saved['run_id'] in self.delivery.runs:
                return {'session_id': session_id, 'run_id': saved['run_id']}
            if (session_id, body.request_id) not in self.delivery.pending_receipts:
                return {'session_id': session_id, 'status': saved['status'], 'replayed': True}
        elif (value.get('last_reply_id') != body.expected_reply_id
                or value.get('conversation_revision') != body.expected_conversation_revision):
            raise WebError('regeneration_stale')
        return await self.delivery.regenerate(session_id, body, [], can_run=value['can_run'])

    async def compact(self, session_id, body):
        value = await self._session_status(session_id)
        return await self.delivery.compact(session_id, body, [], can_run=value['can_run'], conversation_revision=value['conversation_revision'])

    def session_control_options(self):
        result = {**self.application.get_session_control_options(), 'tools': [
            {'name': spec.tool.name, 'description': spec.tool.description}
            for spec in self.application.get_registered_tools()]}
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def select_tools(self, session_id, names):
        value = await self._session_status(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        await self._apply_setting(self.application.aset_active_tools(session_id, names),
            invalid_code='invalid_tool_selection', invalid_event='web_tool_selection_invalid',
            failure_event='web_tool_selection_failed')
        return {'active_tools': list(self.application.get_active_tools(session_id))}

    async def select_permission_mode(self, session_id, mode):
        value = await self._session_status(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        await self._apply_setting(self.application.aset_permission_mode(session_id, mode),
            invalid_code='permission_mode_unavailable', invalid_event='web_permission_mode_unavailable',
            failure_event='web_permission_mode_failed')
        return {'permission_mode': self.application.get_permission_mode(session_id)}

    async def _apply_setting(self, operation, *, invalid_code, invalid_event, failure_event):
        try:
            await operation
        except ValueError:
            logger.warning(invalid_event)
            raise WebError(invalid_code, 422) from None
        except Exception as error:
            logger.error('%s error_type=%s', failure_event, type(error).__name__)
            raise public_error(error) from None

    async def sessions(self):
        rows = await self.application.alist_workspace_sessions(self.settings.workspace_root)
        return [{**row, 'pinned': bool(row['pinned'])} for row in rows]

    async def update_presentation(self, session_id, body):
        await self._session_status(session_id)
        try:
            await self.application.aupdate_session_presentation(session_id, **body.model_dump(exclude_unset=True))
        except Exception as error:
            logger.error('session_presentation_save_failed error_type=%s', type(error).__name__)
            raise public_error(error) from None
        return next(row for row in await self.sessions() if row['session_id'] == session_id)

    async def reorder_sessions(self, body):
        try:
            await self.application.areorder_sessions(self.settings.workspace_root, body.session_ids)
        except ValueError as error:
            raise WebError(str(error), 409 if str(error) == 'stale_session_order' else 422) from None
        except Exception as error:
            raise public_error(error) from None
        return await self.sessions()

    async def delete_session(self, session_id):
        async with self.delivery.start_lock:
            await self._session_status(session_id)
            active = self.delivery.active_by_session.get(session_id)
            if active is not None and not active.task.done():
                raise WebError('session_busy')

            async def commit():
                await self.application.adelete_session(session_id)
                self.delivery.forget_session(session_id)

            try:
                await await_settled(commit())
            except Exception as error:
                raise public_error(error) from None
            return {'session_id': session_id}

    async def create_session(self, model_id):
        try:
            if self.settings.workspace_root is None:
                identity = await self.application.acreate_session(model_id)
            else:
                identity = await self.application.acreate_workspace_session(model_id, self.settings.workspace_root)
            return {'session_id': identity}
        except Exception as error:
            raise public_error(error) from None

    async def _session_status(self, session_id):
        try:
            value = await self.application.aget_session_status(session_id)
        except Exception as error:
            raise public_error(error) from None
        root = str(self.settings.workspace_root) if self.settings.workspace_root is not None else None
        if value['workspace_root'] != root:
            raise WebError('session_not_found', 404)
        read_only = 'recovery' in value
        batches = [] if read_only else self.application.pending_approval_batches(session_id)
        latest = None if read_only else self.delivery.latest.get(session_id)
        return {**value, 'approval_batches': batches, 'pending_approval': bool(batches),
                'active_run_id': value.get('run_id'), 'latest_run_id': latest}

    async def prepare(self, session_id):
        await self._session_status(session_id)
        try:
            await self.application.aprepare_session(session_id)
        except Exception as error:
            logger.error('web_history_prepare_failed error_type=%s', type(error).__name__)
            raise public_error(error) from None
        return await self.session(session_id)

    async def messages(self, session_id, *, agent_id=None, before=None, limit=None):
        await self._session_status(session_id)
        if agent_id and not any(row['agent_id'] == agent_id for row in await self.subagents(session_id)):
            raise WebError('subagent_not_found', 404)
        try:
            return await history_page(self.application, self.settings, session_id,
                                      agent_id=agent_id, before=before, limit=limit)
        except Exception as error:
            raise public_error(error) from None

    async def message_content(self, session_id, sequence, *, agent_id=None, revision, offset, conversation_revision=0):
        await self._session_status(session_id)
        if agent_id and not any(row['agent_id'] == agent_id for row in await self.subagents(session_id)):
            raise WebError('subagent_not_found', 404)
        try:
            result = await self.application.aget_chat_message(session_id, sequence, agent_id=agent_id,
                revision=revision, offset=offset, conversation_revision=conversation_revision, chars=min(self.settings.history_detail_chars,
                    max(1, self.settings.max_frame_bytes // 12)))
            if encoded_size(result) > self.settings.max_frame_bytes:
                raise WebError('history_message_unavailable', 503)
            return result
        except Exception as error:
            raise public_error(error) from None

    async def message_reasoning(self, session_id, sequence, *, revision, offset, conversation_revision, agent_id=None):
        await self._session_status(session_id)
        if agent_id and not any(row['agent_id'] == agent_id for row in await self.subagents(session_id)):
            raise WebError('subagent_not_found', 404)
        try:
            return await self.application.aget_message_reasoning(session_id, sequence, revision=revision, agent_id=agent_id,
                conversation_revision=conversation_revision, offset=offset, chars=min(self.settings.history_detail_chars,
                    max(1, self.settings.max_frame_bytes // 12)), max_bytes=self.settings.max_frame_bytes * 2)
        except Exception as error:
            logger.warning('web_reasoning_read_failed error_type=%s', type(error).__name__)
            raise public_error(error) from None

    async def file_change(self, session_id, sequence, *, revision, agent_id=None, patch=False, conversation_revision=0):
        await self._session_status(session_id)
        if agent_id and not any(row['agent_id'] == agent_id for row in await self.subagents(session_id)):
            raise WebError('subagent_not_found', 404)
        try:
            from naiyue_test_agent.tools.file_change import change_projection
            artifact = await self.application.aget_file_change(session_id, sequence, revision=revision,
                agent_id=agent_id, conversation_revision=conversation_revision, max_bytes=self.settings.max_frame_bytes * 2)
            projection = change_projection(artifact, preview_bytes=self.settings.max_frame_bytes // 4)
            if projection is None:
                raise WebError('change_unavailable', 404)
            if patch:
                value = artifact.get('patch')
                if artifact.get('patch_omitted') or not isinstance(value, str):
                    raise WebError('patch_unavailable', 404)
                if len(value.encode('utf-8')) > self.settings.max_frame_bytes:
                    raise WebError('view_too_large')
                return value
            if encoded_size(projection) > self.settings.max_frame_bytes:
                raise WebError('view_too_large')
            return projection
        except Exception as error:
            logger.warning('web_file_change_read_failed error_type=%s', type(error).__name__)
            raise public_error(error) from None

    async def session(self, session_id):
        value = await self._session_status(session_id)
        page = await self.messages(session_id)
        latest = value['latest_run_id']
        live = self.delivery.get(latest).view() if latest else None
        if live and live.get('conversation_revision', 0) != value.get('conversation_revision', 0):
            live = None
        page = history_window(page, live['messages'] if live else [], self.settings, session_id)
        if live:
            live = {**live, 'messages': [], 'messages_location': 'session'}
        result = {**value, 'messages': page['messages'], 'live': live,
                  'has_compaction': page['has_compaction'],
                  'history': {key: item for key, item in page.items() if key != 'messages'}}
        if encoded_size(result) > self.settings.max_frame_bytes and live and live.get('context_snapshot') is not None:
            live['context_snapshot'] = None
            logger.warning('web_context_summary_omitted reason=view_limit')
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def start(self, session_id, body):
        value = await self._session_status(session_id)
        # RunDelivery handles the duplicate receipt before checking busy.
        return await self.delivery.start(session_id, body, [], can_run=value['can_run'], conversation_revision=value['conversation_revision'])

    async def usage(self, session_id, *, after_model=None):
        await self._session_status(session_id)
        try:
            result = await self.application.aget_model_usage(session_id,
                model_limit=self.settings.usage_page_models, after_model=after_model)
        except Exception as error:
            logger.error('model_usage_view_read_failed error_type=%s', type(error).__name__)
            raise WebError('usage_view_unavailable', 503) from None
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def diagnostics(self, session_id, *, run_id=None, before=None, after=0):
        await self._session_status(session_id)
        try:
            if run_id is None:
                result = await self.application.alist_diagnostic_runs(session_id,
                    limit=self.settings.diagnostic_page_size, before=before)
            else:
                result = await self.application.aget_diagnostic_events(session_id, run_id,
                    limit=self.settings.diagnostic_page_size, after=after)
        except Exception as error:
            from naiyue_test_agent.application.errors import SessionPersistenceError
            if isinstance(error, SessionPersistenceError) and error.args == ('diagnostic_run_not_found',):
                raise WebError('diagnostic_run_not_found', 404) from None
            logger.error('diagnostic_view_read_failed error_type=%s', type(error).__name__)
            raise WebError('diagnostic_view_unavailable', 503) from None
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def context(self, session_id):
        await self._session_status(session_id)
        try:
            result = await self.application.aget_context_view(session_id)
            latest = self.delivery.latest.get(session_id)
            live = self.delivery.get(latest).context_view() if latest else None
            if live is not None:
                result['latest_request'] = live
                if result['current']['basis'] == 'last_sent':
                    result['current'] = {'basis': 'last_sent', 'model_id': live['model_id'], 'estimate': live['estimate']}
        except Exception as error:
            logger.error('context_view_read_failed error_type=%s', type(error).__name__)
            raise WebError('context_view_unavailable', 503) from None
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def subagents(self, session_id):
        value = await self._session_status(session_id)
        try:
            records = {r['agent_path']: r for r in await self.application.aget_chat_subagents(session_id)}
            latest = self.delivery.latest.get(session_id)
            if latest:
                job = self.delivery.get(latest)
                with job.lock:
                    for path, agent in job.agents.items():
                        source = agent['source']
                        parent = next((m for m in reversed(job.projection.messages)
                            if m['id'] == agent.get('parent_batch_id')), {})
                        call = next((c for c in parent.get('tool_calls', []) if c['id'] == source['parent_tool_call_id']), {})
                        records[path] = {**records.get(path, {}), 'agent_id': source['agent_id'], 'agent_path': path,
                            'agent': path.split('/')[1], 'task': call.get('args', {}).get('task', records.get(path, {}).get('task', '')),
                            'tool_call_id': source['parent_tool_call_id'], 'parent_batch_id': agent.get('parent_batch_id'),
                            'status': agent['status']}
            result = list(records.values())
            if encoded_size(result) > self.settings.max_frame_bytes:
                raise WebError('view_too_large')
            return result
        except WebError:
            raise
        except Exception as error:
            logger.error('subagent_history_read_failed error_type=%s', type(error).__name__)
            raise WebError('subagent_history_unavailable', 503) from None

    async def subagent(self, session_id, agent_id):
        record = next((r for r in await self.subagents(session_id) if r['agent_id'] == agent_id), None)
        if record is None:
            raise WebError('subagent_not_found', 404)
        try:
            page = await history_page(self.application, self.settings, session_id, agent_id=agent_id)
            result = {**record, **page, 'available': bool(page['coverage']['checkpoint_id']), 'tool_states': {}}
            latest = self.delivery.latest.get(session_id)
            live = self.delivery.get(latest).agent_view(record['agent_path']) if latest else None
            if live:
                window = history_window(page, live.get('messages', []), self.settings, session_id, agent_id)
                result.update(status=live['status'], source=live['source'], tool_states=live['tool_states'],
                              **window, available=bool(window['messages']))
            if encoded_size(result) > self.settings.max_frame_bytes:
                raise WebError('view_too_large')
            return result
        except WebError:
            raise
        except Exception as error:
            logger.error('subagent_history_read_failed error_type=%s', type(error).__name__)
            raise WebError('subagent_history_unavailable', 503) from None

    async def select_model(self, session_id, model_id):
        value = await self._session_status(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        try:
            await self.application.aselect_model(session_id, model_id)
        except Exception as error:
            raise public_error(error) from None
        return {'model_id': model_id}

    async def recover(self, session_id, body):
        async with self.delivery.start_lock:
            active = self.delivery.active_by_session.get(session_id)
            if active is not None and not active.task.done():
                raise WebError('session_busy')
            value = await self._session_status(session_id)
            if body.action not in value.get('recovery', {}).get('actions', []):
                raise WebError('session_recovery_blocked')
            if value.get('checkpoint_id') != body.checkpoint_id:
                raise WebError('checkpoint_mismatch')
            try:
                await self.application.arecover_session(session_id, checkpoint_id=body.checkpoint_id, action=body.action)
            except Exception as error:
                raise public_error(error) from None
            return await self.session(session_id)

    async def approve(self, session_id, body):
        await self._session_status(session_id)
        async def check_current():
            value = await self._session_status(session_id)
            batch = next((batch for batch in value['approval_batches']
                if batch['approval_batch_id'] == body.approval_batch_id and batch['origin_run_id'] == body.origin_run_id), None)
            if value['manual_review_required'] or batch is None:
                raise WebError('stale_approval')
            prepared = {'kind': batch['kind'], 'messages': [], 'conversation_revision': value['conversation_revision']}
            if batch['kind'] == 'paused_root':
                try:
                    prepared['decisions'], prepared['remember_approved'] = self.application.prepare_root_approval_submission(
                        session_id, approval_batch_id=body.approval_batch_id, origin_run_id=body.origin_run_id,
                        decisions=[item.model_dump() for item in body.decisions], remember_approved=body.remember_approved)
                except ValueError:
                    raise WebError('stale_approval') from None
                prepared['messages'] = await self.application.aget_approval_seed(session_id)
            return prepared
        # Re-read under the same admission lock as start; duplicate receipts are checked first.
        return await self.delivery.resume(session_id, body, check_current)

    async def stop_approval(self, session_id, body):
        await self._session_status(session_id)
        async def check_current():
            value = await self._session_status(session_id)
            batch = next((batch for batch in value['approval_batches'] if batch['kind'] == 'paused_root'
                and batch['approval_batch_id'] == body.approval_batch_id and batch['origin_run_id'] == body.origin_run_id), None)
            if value['manual_review_required'] or batch is None:
                raise WebError('stale_approval')
            return {'kind': 'paused_root', 'messages': await self.application.aget_approval_seed(session_id), 'stop': True, 'conversation_revision': value['conversation_revision']}
        return await self.delivery.resume(session_id, body, check_current)
