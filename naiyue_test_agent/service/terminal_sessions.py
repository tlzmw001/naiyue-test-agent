"""Bounded terminal history facts and server-owned output cleanup confirmations."""
from hashlib import sha256
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from naiyue_test_agent.web.dependencies import get_service
from naiyue_test_agent.web.errors import WebError
from naiyue_test_agent.web.serialization import message_view

router = APIRouter(prefix='/terminal')


@router.get('/sessions/{session_id}/history')
async def history(session_id: str, service=Depends(get_service)):
    await service._session_status(session_id)
    value = await service.application.history.session(session_id)
    return {**{key: item for key, item in value.items() if key != 'messages'},
            'message_count': len(value['messages'])}


@router.get('/sessions/{session_id}/message')
async def message(session_id: str, index: int = Query(ge=0), offset: int = Query(default=0, ge=0),
                  checkpoint: str | None = Query(default=None, max_length=128), service=Depends(get_service)):
    await service._session_status(session_id)
    value = await service.application.history.session(session_id)
    if checkpoint is not None and value['checkpoint_id'] != checkpoint:
        raise WebError('history_cursor_stale')
    if index >= len(value['messages']):
        raise WebError('history_message_not_found', 404)
    saved = value['messages'][index]
    row = message_view(saved)
    row['has_non_text'] = isinstance(saved.content, list) and any(
        isinstance(block, dict) and block.get('type') != 'text' for block in saved.content)
    text = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
    if offset > len(text):
        raise WebError('history_invalid_offset', 422)
    chunk = text[offset:offset + max(1, service.settings.max_frame_bytes // 12)]
    return {'text': chunk, 'next_offset': offset + len(chunk),
            'complete': offset + len(chunk) == len(text), 'sha256': sha256(text.encode()).hexdigest()}


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    confirmation_id: str


@router.post('/outputs/preview')
async def preview(request: Request, service=Depends(get_service)):
    scopes = request.app.state.scopes
    if len(scopes.output_plans) >= scopes.settings.max_receipts:
        raise WebError('output_confirmation_window_full')
    plan = await service.application.tasks.preview_outputs(service.settings.workspace_root)
    identity = str(uuid4())
    scopes.output_plans[identity] = (request.path_params['scope_id'], plan)
    return {'confirmation_id': identity, 'file_count': plan.file_count, 'total_bytes': plan.total_bytes}


@router.post('/outputs/clear')
async def clear(body: Confirmation, request: Request, service=Depends(get_service)):
    plans = request.app.state.scopes.output_plans
    saved = plans.get(body.confirmation_id)
    if saved is None or saved[0] != request.path_params['scope_id']:
        raise WebError('output_confirmation_invalid')
    del plans[body.confirmation_id]
    count, size = await service.application.tasks.clear_outputs(saved[1])
    return {'file_count': count, 'total_bytes': size}
