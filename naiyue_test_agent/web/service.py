"""Workspace-scoped public application operations for the HTTP adapter."""

import logging

from .errors import WebError, public_error
from .serialization import encoded_size, message_view
from naiyue_test_agent.application.session_storage import SessionPersistenceError
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.agent_runtime.async_utils import await_settled


logger = logging.getLogger(__name__)


class WebService:
    def __init__(self, application, delivery, settings):
        self.application, self.delivery, self.settings = application, delivery, settings

    def models(self):
        return {'default_model_id': self.application.default_model_id,
                'model_ids': list(self.application.model_ids), 'thinking_options': self.application.get_thinking_options()}

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
        value = await self.session(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        try:
            await self.application.aselect_thinking(session_id, model_id, level)
        except ValueError:
            logger.warning('web_thinking_selection_invalid')
            raise WebError('thinking_unavailable', 422) from None
        except Exception as error:
            logger.error('web_thinking_selection_failed error_type=%s', type(error).__name__)
            raise public_error(error) from None
        return {'model_id': model_id, 'thinking_level': level}

    async def compact(self, session_id, body):
        value = await self.session(session_id)
        return await self.delivery.compact(session_id, body, value['messages'], can_run=value['can_run'])

    def session_control_options(self):
        result = {**self.application.get_session_control_options(), 'tools': [
            {'name': spec.tool.name, 'description': spec.tool.description}
            for spec in self.application.get_registered_tools()]}
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def select_tools(self, session_id, names):
        value = await self.session(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        try:
            await self.application.aset_active_tools(session_id, names)
        except ValueError:
            logger.warning('web_tool_selection_invalid')
            raise WebError('invalid_tool_selection', 422) from None
        except Exception as error:
            logger.error('web_tool_selection_failed error_type=%s', type(error).__name__)
            raise public_error(error) from None
        return {'active_tools': list(self.application.get_active_tools(session_id))}

    async def select_permission_mode(self, session_id, mode):
        value = await self.session(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        try:
            await self.application.aset_permission_mode(session_id, mode)
        except ValueError:
            logger.warning('web_permission_mode_unavailable')
            raise WebError('permission_mode_unavailable', 422) from None
        except Exception as error:
            logger.error('web_permission_mode_failed error_type=%s', type(error).__name__)
            raise public_error(error) from None
        return {'permission_mode': self.application.get_permission_mode(session_id)}

    async def sessions(self):
        rows = await self.application.alist_sessions()
        return [{**{key: row[key] for key in ('session_id', 'model_id', 'workspace_root',
            'state', 'updated_at', 'last_run_status', 'title')}, 'pinned': bool(row['pinned'])} for row in rows
            if row['workspace_root'] == (str(self.settings.workspace_root) if self.settings.workspace_root else None)]

    async def update_presentation(self, session_id, body):
        if not any(row['session_id'] == session_id for row in await self.sessions()):
            raise WebError('session_not_found', 404)
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
            if not any(row['session_id'] == session_id for row in await self.sessions()):
                raise WebError('session_not_found', 404)
            if self.delivery.active is not None and not self.delivery.active.task.done():
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

    async def session(self, session_id):
        rows = await self.sessions()
        row = next((item for item in rows if item['session_id'] == session_id), None)
        if row is None:
            raise WebError('session_not_found', 404)
        root = str(self.settings.workspace_root) if self.settings.workspace_root else None
        if row['workspace_root'] != root:
            raise WebError('workspace_mismatch', 403)
        try:
            value = None
            if not self.application.has_session(session_id):
                if row['state'] == 'ready':
                    try:
                        await self.application.arestore_session(session_id)
                    except (SessionPersistenceError, ModelConfigError, ValueError):
                        value = await self.application.aget_session_history(session_id)
                else:
                    value = await self.application.aget_session_history(session_id)
            if value is None:
                value = await self.application.aget_session_view(session_id)
        except Exception as error:
            raise public_error(error) from None
        messages = [message_view(m) for m in value.pop('messages')]
        has_compaction = any(m.get('is_context_summary') for m in messages)
        read_only = 'recovery' in value
        latest = None if read_only else self.delivery.latest.get(session_id)
        if latest is not None:
            live = self.delivery.get(latest).view()
            messages = live['messages']
        else:
            live = None
        batches = [] if read_only else self.application.pending_approval_batches(session_id)
        value.pop('approval_run_id', None)
        result = {**row, **value, 'messages': messages, 'approval_batches': batches,
            'pending_approval': bool(batches),
            'has_compaction': has_compaction,
            'active_run_id': value.get('run_id'), 'latest_run_id': latest,
            'can_run': not read_only and not value['busy'] and row['state'] != 'running'
                and not value['manual_review_required'] and not value['pending_approval'],
            'live': live}
        if read_only:
            result.update(busy=False, manual_review_required=row['state'] == 'review_required',
                          pending_approval=False)
        if (encoded_size(result) > self.settings.max_frame_bytes and result.get('live')
                and result['live'].get('context_snapshot') is not None):
            result['live']['context_snapshot'] = None
            logger.warning('web_context_summary_omitted reason=view_limit')
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def start(self, session_id, body):
        value = await self.session(session_id)
        # RunDelivery handles the duplicate receipt before checking busy.
        return await self.delivery.start(session_id, body, value['messages'], can_run=value['can_run'])

    async def context(self, session_id):
        if not any(row['session_id'] == session_id for row in await self.sessions()):
            raise WebError('session_not_found', 404)
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
        value = await self.session(session_id)
        try:
            records = await self.application.aget_subagents(session_id)
        except Exception as error:
            logger.error('subagent_history_read_failed error_type=%s', type(error).__name__)
            raise WebError('subagent_history_unavailable', 503) from None
        live = (value.get('live') or {}).get('agents', {})
        for row in records:
            if row['agent_path'] in live:
                row['status'] = live[row['agent_path']]['status']
        if encoded_size(records) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return records

    async def subagent(self, session_id, agent_id):
        await self.session(session_id)
        try:
            result = await self.application.aget_subagent_history(session_id, agent_id)
        except LookupError:
            raise WebError('subagent_not_found', 404) from None
        except Exception as error:
            logger.error('subagent_history_read_failed error_type=%s', type(error).__name__)
            raise WebError('subagent_history_unavailable', 503) from None
        result['messages'] = [message_view(m) for m in result['messages']]
        result['tool_states'] = {}
        latest = self.delivery.latest.get(session_id)
        live = self.delivery.get(latest).agent_view(result['agent_path']) if latest else None
        if live:
            rows = {row['id']: row for row in result['messages']}
            rows.update({row['id']: row for row in live.get('messages', [])})
            result.update(status=live['status'], source=live['source'], tool_states=live['tool_states'],
                          messages=list(rows.values()), available=bool(rows))
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def select_model(self, session_id, model_id):
        value = await self.session(session_id)
        if 'recovery' in value:
            raise WebError('session_recovery_blocked')
        try:
            await self.application.aselect_model(session_id, model_id)
        except Exception as error:
            raise public_error(error) from None
        return {'model_id': model_id}

    async def recover(self, session_id, body):
        async with self.delivery.start_lock:
            active = self.delivery.active
            if active is not None and not active.task.done():
                raise WebError('session_busy')
            value = await self.session(session_id)
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
        await self.session(session_id)
        async def check_current():
            value = await self.session(session_id)
            batch = next((batch for batch in value['approval_batches']
                if batch['approval_batch_id'] == body.approval_batch_id and batch['origin_run_id'] == body.origin_run_id), None)
            if value['manual_review_required'] or batch is None:
                raise WebError('stale_approval')
            prepared = {'kind': batch['kind'], 'messages': value['messages']}
            if batch['kind'] == 'paused_root':
                try:
                    prepared['decisions'], prepared['remember_approved'] = self.application.prepare_root_approval_submission(
                        session_id, approval_batch_id=body.approval_batch_id, origin_run_id=body.origin_run_id,
                        decisions=[item.model_dump() for item in body.decisions], remember_approved=body.remember_approved)
                except ValueError:
                    raise WebError('stale_approval') from None
            return prepared
        # Re-read under the same admission lock as start; duplicate receipts are checked first.
        return await self.delivery.resume(session_id, body, check_current)

    async def stop_approval(self, session_id, body):
        await self.session(session_id)
        async def check_current():
            value = await self.session(session_id)
            batch = next((batch for batch in value['approval_batches'] if batch['kind'] == 'paused_root'
                and batch['approval_batch_id'] == body.approval_batch_id and batch['origin_run_id'] == body.origin_run_id), None)
            if value['manual_review_required'] or batch is None:
                raise WebError('stale_approval')
            return {'kind': 'paused_root', 'messages': value['messages'], 'stop': True}
        return await self.delivery.resume(session_id, body, check_current)
