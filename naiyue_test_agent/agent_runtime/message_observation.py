"""Observe complete state messages without owning their reducer or persistence."""

from .observation import observe, emit_event

import hashlib
import json
import logging

from langchain_core.messages import (
    AIMessage, ChatMessage, FunctionMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langgraph.errors import GraphBubbleUp
from langgraph.pregel import Pregel
from langgraph.types import Command

from .events import (
    ContextMessagesChangedData, MessageSnapshotData, MessageSnapshotUnavailableData,
    safe_error_type,
)


logger = logging.getLogger(__name__)
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


def invoke_with_message_observation(agent, input, *, context, config, publisher):
    """Consume one native synchronous execution and preserve invoke's v1 result."""
    observer = MessageStateObserver(publisher)
    turns = context.get('_turn_observer')
    controls = context.get('_control_observer')
    context_operations = context.get('_context_observer')
    if (not isinstance(agent, Pregel)
            or getattr(agent.invoke, '__func__', None) is not Pregel.invoke):
        observe(observer, observer.unavailable, 'stream', 'unsupported_state_interface')
        if turns is not None:
            observe(turns, turns.disable)
        return agent.invoke(input, context=context, config=config)
    baseline_messages = []
    if agent.checkpointer:
        try:
            baseline = agent.get_state(config)
        except GraphBubbleUp:
            raise
        except Exception as error:
            observe(observer, observer.unavailable, 'baseline', 'baseline_read_failed', error=error)
            if turns is not None:
                observe(turns, turns.disable)
            return agent.invoke(input, context=context, config=config)
        baseline_messages = baseline.values.get('messages', [])
        observe(observer, observer.seed, baseline_messages)
    if turns is not None and isinstance(input, Command):
        observe(turns, turns.activate_resume, baseline_messages)
    latest, interrupts = None, []
    iterator = agent.stream(input, context=context, config=config,
                            stream_mode=['updates', 'values'], version='v2',
                            output_keys=agent.output_channels)
    primary = None
    try:
        for part in iterator:
            if part['ns']:
                continue
            if part['type'] == 'values':
                latest = part['data']
                if isinstance(latest, dict):
                    observe(observer, observer.observe, latest.get('messages', []))
                    if controls is not None:
                        observe(controls, controls.observe, latest.get('messages', []))
                    if context_operations is not None:
                        observe(context_operations, context_operations.observe, latest.get('messages', []))
                    if turns is not None:
                        observe(turns, turns.observe, latest.get('messages', []))
            elif part['type'] == 'updates' and isinstance(part['data'], dict):
                interrupts.extend(part['data'].get('__interrupt__', ()))
            if turns is not None:
                observe(turns, turns.try_finish)
            if context_operations is not None:
                observe(context_operations, context_operations.flush)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            close = getattr(iterator, 'close', None)
            if close is not None:
                close()
        except BaseException as error:
            if primary is None:
                raise
            logger.error('message_stream_close_failed error_type=%s', safe_error_type(error))
        if isinstance(primary, Exception) and not isinstance(primary, GraphBubbleUp) and agent.checkpointer:
            try:
                recovered = agent.get_state(config)
                observe(observer, observer.observe, recovered.values.get('messages', []), visibility='recovery_view')
                if controls is not None:
                    observe(controls, controls.observe, recovered.values.get('messages', []), visibility='recovery_view')
                if context_operations is not None:
                    observe(context_operations, context_operations.observe, recovered.values.get('messages', []), visibility='recovery_view')
                if turns is not None:
                    observe(turns, turns.observe, recovered.values.get('messages', []), visibility='recovery_view')
            except BaseException as error:
                logger.error('message_recovery_observation_failed error_type=%s', safe_error_type(error))
                if isinstance(error, Exception) and not isinstance(error, GraphBubbleUp):
                    try:
                        observe(observer, observer.unavailable, 'recovery', 'recovery_read_failed',
                                             visibility='recovery_view', error=error)
                    except BaseException as reporting_error:
                        logger.error('message_recovery_reporting_failed error_type=%s',
                                     safe_error_type(reporting_error))
    if interrupts:
        return {**(latest if isinstance(latest, dict) else {}), '__interrupt__': interrupts}
    return latest
