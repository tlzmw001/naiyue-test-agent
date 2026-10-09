"""Immutable display projection and ID-based state observation."""

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
from naiyue_test_agent.agent_runtime.message_observation import MessageStateObserver


def observer():
    events = []
    publisher = RunEventPublisher('session', 'run', (events.append,))
    publisher.publish('run_started', RunStartedData('message'))
    return MessageStateObserver(publisher), events


def test_snapshot_is_independent_frozen_projection_not_provider_wire():
    state, events = observer()
    message = AIMessage(id='ai-1', content=[{'type': 'text', 'text': 'PRIVATE_BODY'}],
                        tool_calls=[{'name': 'read', 'args': {'path': 'PRIVATE_PATH'}, 'id': 'c1'}],
                        response_metadata={'secret': 'PRIVATE_METADATA'},
                        additional_kwargs={'secret': 'PRIVATE_EXTRA'})
    state.observe([message])
    data = events[-1].data
    row = json.loads(data.snapshot_json)
    assert row == {'id': 'ai-1', 'type': 'ai', 'content': message.content,
                   'tool_calls': message.tool_calls, 'invalid_tool_calls': [],
                   'is_context_summary': False}
    assert 'PRIVATE_METADATA' not in data.snapshot_json and 'PRIVATE_EXTRA' not in data.snapshot_json
    assert data.snapshot_bytes == len(data.snapshot_json.encode('utf-8'))
    assert data.snapshot_sha256 == hashlib.sha256(data.snapshot_json.encode('utf-8')).hexdigest()
    with pytest.raises(FrozenInstanceError):
        data.snapshot_json = '{}'
    message.content[0]['text'] = 'changed'
    message.tool_calls[0]['args']['path'] = 'changed'
    assert json.loads(data.snapshot_json)['content'][0]['text'] == 'PRIVATE_BODY'
    state.observe([message])
    assert events[-1].event_type == 'message_revised'
    state.observe([message.model_copy(deep=True)])
    assert len(events) == 3


def test_seed_skips_history_and_compaction_reports_removed_ids_only():
    state, events = observer()
    old = HumanMessage(id='old', content='old')
    kept = HumanMessage(id='kept', content='kept')
    state.seed([old, kept])
    state.observe([old, kept, AIMessage(id='answer', content='done')])
    summary = HumanMessage(id='summary', content='summary', additional_kwargs={
        'naiyue_compaction_summary': True,
    })
    state.observe([summary, kept, AIMessage(id='answer', content='done')])
    completed = [e.data for e in events if e.event_type == 'message_completed']
    assert [d.message_id for d in completed] == ['answer', 'summary']
    assert completed[-1].is_context_summary is True
    changed = events[-1]
    assert changed.event_type == 'context_messages_changed'
    assert changed.data.removed_message_ids == ('old',)
    assert changed.data.current_message_ids == ('summary', 'kept', 'answer')
    assert changed.data.reason == 'compaction'
    assert changed.data.removed_count == 1 and changed.data.current_count == 3
    state.observe([summary, kept])
    assert events[-1].data.reason == 'state_update'


def test_tool_error_is_complete_content_and_keeps_tool_call_identifier():
    state, events = observer()
    state.observe([ToolMessage(id='result', content='Bad arguments: missing path',
                               tool_call_id='call', status='error', artifact=object())],
                  visibility='recovery_view')
    data = events[-1].data
    assert data.visibility == 'recovery_view' and data.tool_call_id == 'call'
    assert data.status == 'error' and data.message_type == 'tool'
    assert json.loads(data.snapshot_json)['content'] == 'Bad arguments: missing path'
    assert 'artifact' not in data.snapshot_json


def test_unavailable_projection_does_not_invent_content_or_removed_ids():
    state, events = observer()
    original = HumanMessage(id='old', content='old')
    state.seed([original])
    bad = HumanMessage(id='bad', content=[{'type': 'custom', 'data': object()}])
    state.observe([original, bad])
    state.observe([original, bad])
    assert [e.event_type for e in events] == ['run_started', 'message_snapshot_unavailable']
    assert events[-1].data.reason == 'unsupported_projection'
    assert events[-1].data.error_type == 'TypeError'
    state.observe([HumanMessage(content='unassigned')])
    assert events[-1].data.reason == 'missing_message_id'
    assert not any(e.event_type == 'context_messages_changed' for e in events)
    bad.content = 'now supported'
    state.observe([original, bad])
    assert events[-1].event_type == 'message_completed'


def test_same_id_changed_from_baseline_is_revision():
    state, events = observer()
    state.seed([HumanMessage(id='same', content='before')])
    state.observe([HumanMessage(id='same', content='after')])
    assert events[-1].event_type == 'message_revised'
    assert json.loads(events[-1].data.snapshot_json)['content'] == 'after'
