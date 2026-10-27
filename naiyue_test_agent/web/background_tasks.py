"""Authenticated management of this Web application's workspace tasks."""

import logging

from fastapi import APIRouter, Path, Query, Request

from naiyue_test_agent.application.sessions.background_tasks import TaskQueryError
from .errors import WebError
from .dependencies import get_service
from .serialization import encoded_size


logger = logging.getLogger(__name__)
router = APIRouter(prefix='/background-tasks')
TASK_ID = r'^[0-9a-f]{32}$'


def _bounded(value, budget):
    if encoded_size(value) <= budget:
        return value
    if 'output' in value:
        text = value['output']
        value = {**value, 'output': '', 'truncated': True}
        if encoded_size(value) <= budget:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                value['output'] = text[-middle:]
                if encoded_size(value) <= budget:
                    low = middle
                else:
                    high = middle - 1
            value['output'] = text[-low:] if low else ''
            return value
    raise WebError('view_too_large')


async def _perform(request, action, *, session_id=None, task_id=None, limit=20, before=None):
    service = await get_service(request)
    root = service.settings.workspace_root
    if root is None and service.settings.workspace_id is None:
        if action != 'list':
            raise WebError('task_not_found', 404)
        return {'items': [], 'next_before': None, 'active_count': 0, 'terminal_revision': 0, 'revision': 'empty'}
    if session_id is not None:
        await service._session_status(session_id)
    try:
        scope = {'session_id': session_id, 'workspace_root': None if service.settings.workspace_id is not None else root}
        if action == 'list':
            value = await service.application.tasks.list(**scope, limit=limit, before=before)
        elif action == 'output':
            value = await service.application.tasks.output(task_id, **scope)
        else:
            value = await service.application.tasks.stop(task_id, **scope)
            if not value['cleanup_confirmed']:
                raise WebError('task_cleanup_unconfirmed')
        return _bounded(value, service.settings.max_frame_bytes)
    except TaskQueryError as error:
        raise WebError(error.code, 404 if error.code == 'task_not_found' else 422) from None
    except WebError:
        raise
    except Exception as error:
        logger.error('web_background_task_failed action=%s error_type=%s', action, type(error).__name__)
        raise WebError('background_tasks_unavailable', 503) from None


@router.get('')
async def tasks(request: Request, session_id: str | None = Query(default=None, min_length=1, max_length=128),
                limit: int = Query(default=20, ge=1, le=100),
                before: str | None = Query(default=None, pattern=TASK_ID)):
    return await _perform(request, 'list', session_id=session_id, limit=limit, before=before)


@router.get('/{task_id}/output')
async def output(request: Request, task_id: str = Path(pattern=TASK_ID),
                 session_id: str | None = Query(default=None, min_length=1, max_length=128)):
    return await _perform(request, 'output', task_id=task_id, session_id=session_id)


@router.post('/{task_id}/stop')
async def stop(request: Request, task_id: str = Path(pattern=TASK_ID),
               session_id: str | None = Query(default=None, min_length=1, max_length=128)):
    return await _perform(request, 'stop', task_id=task_id, session_id=session_id)
