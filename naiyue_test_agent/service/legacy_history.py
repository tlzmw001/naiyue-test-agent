"""Authenticated read-only legacy history; it never registers or opens a runtime host."""
from hashlib import sha256
import json
from pathlib import Path
from fastapi import APIRouter, Query, Request

from naiyue_test_agent.application.conversation.legacy_history import legacy_history, legacy_sessions
from naiyue_test_agent.web.errors import WebError
from naiyue_test_agent.web.serialization import message_view

router = APIRouter(prefix='/api/v1/terminal/legacy')


@router.get('')
async def sessions(database_path: Path, root: Path | None = None):
    return await legacy_sessions(database_path, root=root)


@router.get('/history')
async def history(database_path: Path, session_id: str = Query(min_length=1, max_length=128)):
    value = await legacy_history(database_path, session_id)
    return {**{key: item for key, item in value.items() if key != 'messages'},
            'message_count': len(value['messages'])}


@router.get('/message')
async def message(request: Request, database_path: Path, session_id: str = Query(min_length=1, max_length=128),
                  index: int = Query(ge=0), offset: int = Query(default=0, ge=0),
                  checkpoint: str | None = Query(default=None, max_length=128)):
    value = await legacy_history(database_path, session_id)
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
    chunk = text[offset:offset + max(1, request.app.state.web_settings.max_frame_bytes // 12)]
    return {'text': chunk, 'next_offset': offset + len(chunk), 'complete': offset + len(chunk) == len(text),
            'sha256': sha256(text.encode()).hexdigest()}
