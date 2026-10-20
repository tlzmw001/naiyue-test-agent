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


def _snapshot(message, visibility, *, reply_id=None):
    summary = (isinstance(message, HumanMessage)
               and message.additional_kwargs.get('naiyue_compaction_summary') is True)
    row = {'type': message.type, 'id': message.id, 'content': message.content,
           'is_context_summary': summary}
    identity = reply_id or message.additional_kwargs.get('naiyue_reply_id')
    if identity:
        row['reply_id'] = identity
    if message.name is not None:
        row['name'] = message.name
    if isinstance(message, AIMessage):
        from naiyue_test_agent.model_access.reasoning import visible_reasoning
        reasoning = visible_reasoning(message)
        if reasoning:
            row['reasoning'] = reasoning
        row.update(tool_calls=message.tool_calls, invalid_tool_calls=message.invalid_tool_calls)
    if isinstance(message, ToolMessage):
        from naiyue_test_agent.tools.file_change import change_projection
        change = change_projection(message.artifact)
        if change is not None:
            row['file_change'] = change
        from naiyue_test_agent.mcp_client.presentation import result_projection
        mcp = result_projection(message.artifact, name=message.name)
        if mcp is not None:
            row['mcp_result'] = mcp
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
        call_replies = {}
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
                if isinstance(message, AIMessage):
                    call_replies.update({call['id']: message.additional_kwargs.get('naiyue_reply_id') for call in message.tool_calls})
                data = _snapshot(message, visibility, reply_id=call_replies.get(message.tool_call_id) if isinstance(message, ToolMessage) else None)
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
