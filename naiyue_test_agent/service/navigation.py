"""Historical sidebar metadata never opens model-backed scope services."""
from uuid import UUID

from fastapi import APIRouter, Request

from naiyue_test_agent.contracts.projects import ProjectError

router = APIRouter(prefix='/api/v1/navigation')


@router.get('/{scope_id}/sessions')
async def sessions(scope_id: str, request: Request):
    application = request.app.state.application
    if scope_id == 'chats':
        rows = await application.chats.list_sessions()
    else:
        try:
            if str(UUID(scope_id)) != scope_id:
                raise ValueError('invalid_id')
        except ValueError:
            raise ProjectError('project_not_found') from None
        rows = await application.projects.list_sessions(scope_id)
    fields = ('session_id', 'model_id', 'workspace_root', 'project_id', 'execution_root_kind', 'execution_binding',
              'state', 'updated_at', 'last_run_status', 'title')
    return [{**{key: row[key] for key in fields}, 'pinned': bool(row['pinned'])} for row in rows]
