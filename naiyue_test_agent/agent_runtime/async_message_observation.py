"""Consume native graph streams; final messages and deltas have distinct roles."""

from .observation import observe, emit_event

import logging

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langgraph.errors import GraphBubbleUp
from langgraph.pregel import Pregel
from langgraph.types import Command

from naiyue_test_agent.contracts.failures import safe_error_type, read_failure_description
from naiyue_test_agent.model_access.messages import content_text
from .message_observation import MessageStateObserver, _snapshot
from .stream_events import MessageReasoningDeltaData, MessageDeltaData, SUMMARY_TAG, PERMISSION_REVIEW_TAG
from .tool_execution.observation import observe_tool_progress


logger = logging.getLogger(__name__)


class _StreamState:
    def __init__(self, publisher, context):
        self.publisher = publisher
        self.observer = MessageStateObserver(publisher)
        self.context = context
        self.indices = {}
        self.reasoning_indices = {}
        self.stream_ids = {}
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
        if not isinstance(message.id, str) or not message.id:
            observe(self.observer, self.observer.unavailable, 'snapshot', 'missing_message_id', message=message)
            return
        from naiyue_test_agent.model_access.reasoning import visible_reasoning
        reasoning = visible_reasoning(message)
        text = content_text(message.content, delta=True)
        key = (metadata.get('langgraph_checkpoint_ns'), metadata.get('langgraph_step'))
        # Responses announces its final response ID on an empty first chunk.
        # Later text chunks may carry framework run IDs; aggregate state keeps
        # the first response ID. Retain that anchor before filtering empty text.
        if key not in self.stream_ids or (not text
                and message.response_metadata.get('id') == message.id):
            self.stream_ids[key] = message.id
        identity = self.stream_ids[key]
        if reasoning:
            self.reasoning_indices[identity] = self.reasoning_indices.get(identity, 0) + 1
            self.drafts.add(identity)
            emit_event(self.publisher, 'message_reasoning_delta', lambda: MessageReasoningDeltaData(
                identity, reasoning, self.reasoning_indices[identity], reply_id=(self.latest or {}).get('active_reply_id')))
        if not text:
            return
        self.indices[identity] = self.indices.get(identity, 0) + 1
        self.drafts.add(identity)
        emit_event(self.publisher, 'message_delta', lambda: MessageDeltaData(
            identity, text, self.indices[identity], reply_id=(self.latest or {}).get('active_reply_id')))


async def ainvoke_with_message_observation(agent, input, *, context, config, publisher, durability=None):
    options = {'durability': durability} if durability is not None else {}
    stream = _StreamState(publisher, context)
    turns = context.get('_turn_observer')
    if not isinstance(agent, Pregel) or getattr(agent.ainvoke, '__func__', None) is not Pregel.ainvoke:
        observe(stream.observer, stream.observer.unavailable, 'stream', 'unsupported_state_interface')
        if turns is not None:
            observe(turns, turns.disable)
        return await agent.ainvoke(input, context=context, config=config, **options)
    baseline = []
    if agent.checkpointer:
        try:
            baseline = (await agent.aget_state(config)).values.get('messages', [])
            incoming = {m.id for m in input.get('messages', []) if isinstance(m, BaseMessage)
                        and m.additional_kwargs.get('naiyue_input_source') == 'regenerated_task'} if isinstance(input, dict) else set()
            observe(stream.observer, stream.observer.seed, [m for m in baseline if m.id not in incoming])
        except GraphBubbleUp:
            raise
        except Exception as error:
            failure = read_failure_description(error, run_id=publisher.run_id)
            if failure is not None and failure.reason == 'subagent_checkpoint_failed':
                raise
            observe(stream.observer, stream.observer.unavailable, 'baseline', 'baseline_read_failed', error=error)
            if turns is not None:
                observe(turns, turns.disable)
            return await agent.ainvoke(input, context=context, config=config, **options)
    if turns is not None and isinstance(input, Command):
        observe(turns, turns.activate_resume, baseline)
    iterator = agent.astream(input, context=context, config=config,
        stream_mode=['messages', 'updates', 'values', 'custom'], version='v2', output_keys=agent.output_channels, **options)
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
        failure = read_failure_description(primary, run_id=publisher.run_id) if primary is not None else None
        if (isinstance(primary, Exception) and not isinstance(primary, GraphBubbleUp) and agent.checkpointer
                and not (failure is not None and failure.reason == 'subagent_checkpoint_failed')):
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
