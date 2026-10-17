"""Observe complete state messages without owning their reducer or persistence."""

from naiyue_test_agent.contracts.failures import safe_error_type

import hashlib
import json

from langchain_core.messages import (
    AIMessage, ChatMessage, FunctionMessage, HumanMessage, SystemMessage, ToolMessage,
)

from .events import (
    ContextMessagesChangedData, MessageSnapshotData, MessageSnapshotUnavailableData,
)


_SUPPORTED = (HumanMessage, AIMessage, ToolMessage, SystemMessage, FunctionMessage, ChatMessage)


def _snapshot(message, visibility):
    summary = (isinstance(message, HumanMessage)
               and message.additional_kwargs.get('naiyue_compaction_summary') is True)
    row = {'type': message.type, 'id': message.id, 'content': message.content,
           'is_context_summary': summary}
    if message.name is not None:
        row['name'] = message.name
    if isinstance(message, AIMessage):
        row.update(tool_calls=message.tool_calls, invalid_tool_calls=message.invalid_tool_calls)
    if isinstance(message, ToolMessage):
        row.update(tool_call_id=message.tool_call_id, status=message.status)
    if isinstance(message, ChatMessage):
        row['role'] = message.role
    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
    body = encoded.encode('utf-8')
    return MessageSnapshotData(
        message.id, message.type, visibility, encoded, hashlib.sha256(body).hexdigest(),
        len(body), summary, row.get('tool_call_id'), row.get('status'),
    )


class MessageStateObserver:
    """Keep only hashes/IDs for this run; subscribers own any retained content."""

    def __init__(self, publisher):
        self.publisher = publisher
        self._hashes = {}
        self._ids = ()
        self._unavailable = set()

    def unavailable(self, stage, reason, *, message=None, visibility=None, error=None):
        message_id = getattr(message, 'id', None)
        message_type = getattr(message, 'type', None)
        message_id = message_id if isinstance(message_id, str) else None
        message_type = message_type if isinstance(message_type, str) else None
        key = (stage, reason, message_id, message_type, visibility)
        if key not in self._unavailable:
            self.publisher.publish('message_snapshot_unavailable', MessageSnapshotUnavailableData(
                stage, reason, message_id, message_type, visibility,
                safe_error_type(error) if error else None,
            ))
            self._unavailable.add(key)

    def seed(self, messages):
        self.observe(messages, baseline=True)

    def observe(self, messages, *, visibility='graph_values', baseline=False):
        if not isinstance(messages, (list, tuple)):
            self.unavailable('baseline' if baseline else 'snapshot',
                             'invalid_message_collection', visibility=visibility)
            return
        ids = []
        seen = set()
        trustworthy = True
        new_summary = False
        for message in messages:
            stage = 'baseline' if baseline else 'snapshot'
            if type(message) not in _SUPPORTED:
                self.unavailable(stage, 'unsupported_message_type', message=message,
                                 visibility=visibility)
                trustworthy = False
                continue
            if not isinstance(message.id, str) or not message.id:
                self.unavailable(stage, 'missing_message_id', message=message, visibility=visibility)
                trustworthy = False
                continue
            if message.id in seen:
                self.unavailable(stage, 'duplicate_message_id', message=message, visibility=visibility)
                trustworthy = False
                continue
            ids.append(message.id)
            seen.add(message.id)
            try:
                data = _snapshot(message, visibility)
            except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as error:
                self.unavailable(stage, 'unsupported_projection', message=message,
                                 visibility=visibility, error=error)
                continue
            previous = self._hashes.get(message.id)
            new_summary |= data.is_context_summary and previous != data.snapshot_sha256
            if not baseline and previous != data.snapshot_sha256:
                self.publisher.publish('message_revised' if previous is not None
                                       else 'message_completed', data)
            self._hashes[message.id] = data.snapshot_sha256
        if trustworthy:
            current = tuple(ids)
            removed = tuple(key for key in self._ids if key not in seen)
            if removed and not baseline:
                self.publisher.publish('context_messages_changed', ContextMessagesChangedData(
                    removed, current, visibility, 'compaction' if new_summary else 'state_update',
                ))
            self._ids = current
