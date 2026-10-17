"""Explicit wire projection; no model SDK objects or credential configuration."""

from dataclasses import asdict, replace
import hashlib
import json
from langchain_core.messages import AIMessage, ToolMessage
from naiyue_test_agent.model_access.messages import content_text
from naiyue_test_agent.agent_runtime.stream_events import ToolProgressData
from naiyue_test_agent.tools.settings import ToolProgressSettings


def wire_event(event):
    return {'version': 1, **asdict(event)}


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))


def text_content(content):
    return content_text(content)


def project_event_for_web(event, *, progress_max_text_bytes=ToolProgressSettings().max_text_bytes):
    """Hash a text-only snapshot copy before Web frame limits; never mutate domain events."""
    if event.event_type == 'tool_progress':
        if type(event.data) is not ToolProgressData:
            raise TypeError('Invalid tool progress payload')
        text = event.data.text.encode('utf-8')[-progress_max_text_bytes:].decode('utf-8', errors='ignore')
        return replace(event, data=replace(event.data, text=text))
    if event.event_type not in ('message_completed', 'message_revised'):
        return event
    row = json.loads(event.data.snapshot_json)
    if not isinstance(row.get('content'), list):
        return event
    row.update(content=text_content(row['content']), content_projection='text')
    snapshot = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
    data = replace(event.data, snapshot_json=snapshot, snapshot_bytes=len(snapshot.encode('utf-8')),
                   snapshot_sha256=hashlib.sha256(snapshot.encode('utf-8')).hexdigest())
    return replace(event, data=data)


def message_view(message):
    summary = bool(message.additional_kwargs.get('naiyue_compaction_summary'))
    row = {'id': message.id, 'type': message.type, 'content': text_content(message.content),
            'is_context_summary': summary, 'provisional': False, 'state': 'complete'}
    if isinstance(message.content, list):
        row['content_projection'] = 'text'
    if isinstance(message, AIMessage):
        row['tool_calls'] = message.tool_calls
    if isinstance(message, ToolMessage):
        row.update(tool_call_id=message.tool_call_id, status=message.status, name=message.name)
    return row
