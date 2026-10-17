"""Consume native graph streams; final messages and deltas have distinct roles."""

from .observation import observe, emit_event

import logging

from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.errors import GraphBubbleUp
from langgraph.pregel import Pregel
from langgraph.types import Command

from naiyue_test_agent.contracts.failures import safe_error_type
from .message_observation import MessageStateObserver, _snapshot
from .stream_events import MessageDeltaData, SUMMARY_TAG, PERMISSION_REVIEW_TAG
from .tool_execution.observation import observe_tool_progress


logger = logging.getLogger(__name__)


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(block.get('text', '') for block in content
                       if isinstance(block, dict) and block.get('type') == 'text'
                       and isinstance(block.get('text'), str))
    return ''


class _StreamState:
    def __init__(self, publisher, context):
        self.publisher = publisher
        self.observer = MessageStateObserver(publisher)
        self.context = context
        self.indices = {}
        self.drafts = set()
        self.latest = None
        self.interrupts = {}

    def state(self, messages, visibility='graph_values'):
        before = {identity: self.observer._hashes.get(identity) for identity in self.drafts}
        observe(self.observer, self.observer.observe, messages, visibility=visibility)
        for message in messages:
            if visibility == 'graph_values' and isinstance(message, AIMessage) and message.id in self.drafts:
                if before.get(message.id) == self.observer._hashes.get(message.id):
                    # A cached or identical revision still confirms a draft from this run.
                    emit_event(self.publisher, 'message_completed', lambda: _snapshot(message, visibility))
                self.drafts.discard(message.id)
        for key in ('_control_observer', '_context_observer', '_turn_observer'):
            item = self.context.get(key)
            if item is not None:
                observe(item, item.observe, messages, visibility=visibility)

    def part(self, part):
        if part['ns']:
            return
        if part['type'] == 'values':
            self.latest = part['data']
            if isinstance(self.latest, dict):
                observe(self, self.state, self.latest.get('messages', []))
            for interrupt in part.get('interrupts', ()):
                self.interrupts[interrupt.id] = interrupt
        elif part['type'] == 'updates' and isinstance(part['data'], dict):
            for interrupt in part['data'].get('__interrupt__', ()):
                self.interrupts[interrupt.id] = interrupt
        elif part['type'] == 'messages':
            observe(self, self.delta, *part['data'])
        elif part['type'] == 'custom':
            observe_tool_progress(self.context, 'accept_custom', part['data'])
        turns, operations = self.context.get('_turn_observer'), self.context.get('_context_observer')
        if turns is not None:
            observe(turns, turns.try_finish)
        if operations is not None:
            observe(operations, operations.flush)

    def delta(self, message, metadata):
        if (metadata.get('langgraph_node') != 'model' or SUMMARY_TAG in metadata.get('tags', ())
                or PERMISSION_REVIEW_TAG in metadata.get('tags', ())
                or not isinstance(message, (AIMessageChunk, AIMessage))):
            return
        text = _text(message.content)
        if not text:
            return
        if not isinstance(message.id, str) or not message.id:
            observe(self.observer, self.observer.unavailable, 'snapshot', 'missing_message_id', message=message)
            return
        self.indices[message.id] = self.indices.get(message.id, 0) + 1
        self.drafts.add(message.id)
        emit_event(self.publisher, 'message_delta', lambda: MessageDeltaData(
            message.id, text, self.indices[message.id]))


async def ainvoke_with_message_observation(agent, input, *, context, config, publisher):
    stream = _StreamState(publisher, context)
    turns = context.get('_turn_observer')
    if not isinstance(agent, Pregel) or getattr(agent.ainvoke, '__func__', None) is not Pregel.ainvoke:
        observe(stream.observer, stream.observer.unavailable, 'stream', 'unsupported_state_interface')
        if turns is not None:
            observe(turns, turns.disable)
        return await agent.ainvoke(input, context=context, config=config)
    baseline = []
    if agent.checkpointer:
        try:
            baseline = (await agent.aget_state(config)).values.get('messages', [])
            observe(stream.observer, stream.observer.seed, baseline)
        except GraphBubbleUp:
            raise
        except Exception as error:
            observe(stream.observer, stream.observer.unavailable, 'baseline', 'baseline_read_failed', error=error)
            if turns is not None:
                observe(turns, turns.disable)
            return await agent.ainvoke(input, context=context, config=config)
    if turns is not None and isinstance(input, Command):
        observe(turns, turns.activate_resume, baseline)
    iterator = agent.astream(input, context=context, config=config,
        stream_mode=['messages', 'updates', 'values', 'custom'], version='v2', output_keys=agent.output_channels)
    primary = None
    try:
        async for part in iterator:
            stream.part(part)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            close = getattr(iterator, 'aclose', None)
            if close is not None:
                await close()
        except BaseException as error:
            if primary is None:
                raise
            logger.error('message_stream_close_failed error_type=%s', safe_error_type(error))
        if isinstance(primary, Exception) and not isinstance(primary, GraphBubbleUp) and agent.checkpointer:
            try:
                snapshot = await agent.aget_state(config)
                observe(stream, stream.state, snapshot.values.get('messages', []), 'recovery_view')
            except BaseException as error:
                logger.error('message_recovery_observation_failed error_type=%s', safe_error_type(error))
                if isinstance(error, Exception) and not isinstance(error, GraphBubbleUp):
                    observe(stream.observer, stream.observer.unavailable, 'recovery', 'recovery_read_failed',
                        visibility='recovery_view', error=error)
    return ({**(stream.latest or {}), '__interrupt__': list(stream.interrupts.values())}
            if stream.interrupts else stream.latest)
