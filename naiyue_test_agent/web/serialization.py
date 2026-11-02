"""Explicit wire projection; no model SDK objects or credential configuration."""

from dataclasses import asdict, replace
import hashlib
import json
from naiyue_test_agent.model_access.messages import content_text
from naiyue_test_agent.agent_runtime.stream_events import ToolProgressData
from naiyue_test_agent.tools.settings import ToolProgressSettings


def wire_event(event):
    return {'version': 1, **asdict(event)}


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))


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
    from naiyue_test_agent.contracts.image_content import input_text
    row.update(content=input_text(row['content']) if row.get('images') else content_text(row['content']), content_projection='text')
    snapshot = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
    data = replace(event.data, snapshot_json=snapshot, snapshot_bytes=len(snapshot.encode('utf-8')),
                   snapshot_sha256=hashlib.sha256(snapshot.encode('utf-8')).hexdigest())
    return replace(event, data=data)



def pending_input_views(rows, settings):
    """A list/receipt is metadata; full retained text has its bounded GET."""
    from .errors import WebError
    chars = min(settings.history_preview_chars, max(0,settings.max_frame_bytes//(max(1,len(rows))*12)))
    result = [{key:value for key,value in row.items() if key != 'legacy_text'} |
        {'text':row['text'][:chars], 'text_truncated':len(row['text'])>chars} for row in rows]
    if encoded_size({'inputs':result}) > settings.max_frame_bytes:
        raise WebError('view_too_large')
    return result
