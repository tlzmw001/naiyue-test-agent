"""Workspace-scoped public application operations for the HTTP adapter."""

from .errors import WebError, public_error
from .serialization import encoded_size, message_view


class WebService:
    def __init__(self, application, delivery, settings):
        self.application, self.delivery, self.settings = application, delivery, settings

    def models(self):
        return {'default_model_id': self.application.default_model_id,
                'model_ids': list(self.application.model_ids)}

    async def sessions(self):
        rows = await self.application.alist_sessions()
        return [{key: row[key] for key in ('session_id', 'model_id', 'workspace_root',
            'state', 'updated_at', 'last_run_status')} for row in rows]

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
            if not self.application.has_session(session_id):
                await self.application.arestore_session(session_id)
            value = await self.application.aget_session_view(session_id)
        except Exception as error:
            raise public_error(error) from None
        messages = [message_view(m) for m in value.pop('messages')]
        has_compaction = any(m.get('is_context_summary') for m in messages)
        latest = self.delivery.latest.get(session_id)
        if latest is not None:
            live = self.delivery.get(latest).view()
            messages = live['messages']
        else:
            live = None
        result = {**row, **value, 'messages': messages,
            'approval_requests': self.application.pending_tool_approvals(session_id),
            'has_compaction': has_compaction,
            'active_run_id': value['run_id'], 'latest_run_id': latest,
            'can_run': not value['busy'] and row['state'] != 'running'
                and not value['manual_review_required'] and not value['pending_approval'],
            'live': live}
        if encoded_size(result) > self.settings.max_frame_bytes:
            raise WebError('view_too_large')
        return result

    async def start(self, session_id, body):
        value = await self.session(session_id)
        # RunDelivery handles the duplicate receipt before checking busy.
        return await self.delivery.start(session_id, body, value['messages'], can_run=value['can_run'])

    async def select_model(self, session_id, model_id):
        await self.session(session_id)
        try:
            await self.application.aselect_model(session_id, model_id)
        except Exception as error:
            raise public_error(error) from None
        return {'model_id': model_id}

    async def approve(self, session_id, body):
        async def check_current():
            value = await self.session(session_id)
            if (value['manual_review_required'] or value['busy'] or value['state'] == 'running'
                    or not value['pending_approval'] or value['approval_run_id'] != body.origin_run_id
                    or [item['tool_call_id'] for item in value['approval_requests']] !=
                       [item.tool_call_id for item in body.decisions]):
                raise WebError('stale_approval')
            return value['messages']
        # Re-read under the same admission lock as start; duplicate receipts are checked first.
        return await self.delivery.resume(session_id, body, check_current)
