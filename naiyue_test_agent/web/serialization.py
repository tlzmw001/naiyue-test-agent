"""Explicit wire projection; no model SDK objects or credential configuration."""

from dataclasses import asdict
import json
from langchain_core.messages import AIMessage, ToolMessage


def wire_event(event):
    return {'version': 1, **asdict(event)}


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(block if isinstance(block, str) else block.get('text', '')
            if isinstance(block, dict) and block.get('type') == 'text' else '' for block in content)
    return ''


def message_view(message):
    summary = bool(message.additional_kwargs.get('naiyue_compaction_summary'))
    row = {'id': message.id, 'type': message.type, 'content': text_content(message.content),
            'is_context_summary': summary, 'provisional': False, 'state': 'complete'}
    if isinstance(message, AIMessage):
        row['tool_calls'] = message.tool_calls
    if isinstance(message, ToolMessage):
        row.update(tool_call_id=message.tool_call_id, status=message.status, name=message.name)
    return row
