"""Prepare one replacement under normal admission, then use the existing run lifecycle."""

import json
import logging
from uuid import uuid4
from langchain_core.messages import HumanMessage, messages_from_dict
from langgraph.graph import START, END
from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking
from .session_recovery import _message_facts
from .session_controls import mark_human_messages
from . import reply_storage

logger = logging.getLogger(__name__)


def _commit_replacement(storage, session, run, request_id, *, config, head, boundary, revision):
    reply_storage.activate(storage, session.session_id, request_id,
        checkpoint=config['configurable']['checkpoint_id'], head_checkpoint=head, boundary=boundary)
    session.activate_regenerated_thread(run, config['configurable']['thread_id'], revision)


async def regenerate(application, session_id, *, request_id, expected_reply_id, expected_conversation_revision):
    storage = application._storage
    existing = await run_blocking(reply_storage.action, storage, session_id, request_id)
    if existing:
        if (existing['reply_id'], existing['revision']) != (expected_reply_id, expected_conversation_revision):
            raise ValueError('request_id_conflict')
        return {**existing, 'replayed': True}
    session, run = application._sessions._admit(session_id, None, 'message')
    activated = False
    original_thread = session.get_thread_id()
    try:
        row = await run_blocking(storage.get, session_id)
        boundary = await run_blocking(reply_storage.latest, storage, session_id)
        if (row['state'] != 'ready' or row['conversation_revision'] != expected_conversation_revision
                or not boundary or boundary['reply_id'] != expected_reply_id):
            raise ValueError('regeneration_stale')
        if session_id in application._persistence._blocked:
            raise ValueError('session_recovery_blocked')
        source = {'configurable': {'thread_id': boundary['source_thread_id'] or boundary['thread_id'],
                                   'checkpoint_id': boundary['source_checkpoint_id']}}
        prefix = []
        if boundary['source_checkpoint_id']:
            saved = await application._agent.aget_state(source)
            if (saved.config or {}).get('configurable', {}).get('checkpoint_id') != boundary['source_checkpoint_id']:
                raise ValueError('regeneration_source_unavailable')
            prefix = saved.values.get('messages', [])
        elif boundary['input_sequence'] != 1:
            raise ValueError('regeneration_source_unavailable')
        if (any(m.additional_kwargs.get('naiyue_reply_id') == expected_reply_id for m in prefix)
                or any(_message_facts(prefix))):
            raise ValueError('regeneration_source_unavailable')
        records = await run_blocking(storage._read, '''SELECT body_json FROM naiyue_chat_messages
            WHERE session_id=? AND thread_id=? AND reply_id=? AND message_type='human' ORDER BY sequence''',
            (session_id, row['thread_id'], expected_reply_id))
        inputs = [m for m in messages_from_dict([json.loads(r['body_json']) for r in records]) if isinstance(m, HumanMessage)]
        if not inputs:
            raise ValueError('regeneration_source_unavailable')
        inputs = mark_human_messages([m.model_copy(update={'id': uuid4().hex, 'additional_kwargs': {
            **{k: v for k, v in m.additional_kwargs.items() if k not in ('naiyue_reply_id', 'naiyue_input_kind')},
            'naiyue_input_source': 'regenerated_task'}}) for m in inputs])
        new_thread = uuid4().hex
        await run_blocking(reply_storage.record_action, storage, session_id, request_id, boundary,
                          expected_conversation_revision, new_thread, run.run_id)
        config = {'configurable': {'thread_id': new_thread}}
        values = {'messages': prefix, 'tool_execution': {}, 'run_stopped': False, 'active_reply_id': None}
        config = await await_settled(application._agent.aupdate_state(config, values, as_node=START))
        config = await await_settled(application._agent.aupdate_state(config, None, as_node=END))
        head = config['configurable']['checkpoint_id']
        # Save the actual replayed human input before activation. A crash must not lose it.
        config = await await_settled(application._agent.aupdate_state(config, {'messages': inputs}, as_node=START))
        config = await await_settled(application._agent.aupdate_state(config, None, as_node=END))
        snapshot = await application._agent.aget_state(config)
        if snapshot.next or snapshot.tasks or run.stop_requested:
            raise ValueError('regeneration_preparation_stopped')
        await await_settled(run_blocking(_commit_replacement, storage, session, run, request_id,
            config=config, head=head, boundary=boundary, revision=expected_conversation_revision + 1))
        activated = True
    except BaseException:
        logger.warning('regeneration_prepare_failed')
        # No model/tool was called during preparation. Settle before releasing the quota.
        if activated or session.get_thread_id() != original_thread:
            session.mark_manual_review(run)
        else:
            try:
                await await_settled(run_blocking(reply_storage.fail_preparation, storage, session_id, request_id))
            except BaseException as secondary:
                logger.error('regeneration_failure_save_failed error_type=%s', type(secondary).__name__)
        session.close_control_window(run)
        session.release_run(run)
        raise
    return await session.arun(application._agent, inputs, run=run)
