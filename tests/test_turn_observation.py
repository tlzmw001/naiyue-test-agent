"""Finite turn state and JSON-only approval continuation."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData, RunFailureData
from naiyue_test_agent.agent_runtime.turn_observation import TurnObserver


def make_observer(run='run'):
    events = []
    pub = RunEventPublisher('session', run, (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    return TurnObserver(pub), events


def test_response_visible_does_not_finish_before_post_model_checks():
    observer, events = make_observer()
    reply = AIMessage(id='ai', content='done')
    observer.begin('model-call')
    observer.accept(reply)
    observer.observe([reply])
    assert [e.event_type for e in events] == ['run_started', 'turn_started']
    observer.mark_ready()
    observer.try_finish()
    assert events[-1].event_type == 'turn_finished' and events[-1].data.status == 'completed'
    assert events[-1].data.assistant_message_id == 'ai'
    observer.try_finish()
    assert len(events) == 3


def test_old_tool_result_cannot_finish_new_tool_request():
    observer, events = make_observer()
    reply = AIMessage(id='ai', content='', tool_calls=[{'name': 'probe', 'args': {}, 'id': 'call'}])
    observer.begin('model-call')
    observer.accept(reply)
    observer.mark_ready()
    old = ToolMessage(id='old', content='old', tool_call_id='call')
    observer.observe([old, reply])
    assert events[-1].event_type == 'turn_started'
    result = ToolMessage(id='new', content='Invalid path', tool_call_id='call', status='error')
    observer.observe([old, reply, result])
    data = events[-1].data
    assert data.status == 'completed' and data.tool_message_ids == ('new',)
    assert data.tool_call_ids == ('call',) and data.error_result_count == 1


def test_suspended_json_roundtrip_resumes_same_turn_in_different_run():
    first, events = make_observer()
    reply = AIMessage(id='ai', content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'call'}])
    first.begin('model-call')
    first.accept(reply)
    first.observe([HumanMessage(id='user', content='read'), reply])
    first.result(interrupted=True)
    saved = json.loads(json.dumps(first.snapshot(pending=True)))
    assert saved['status'] == 'suspended' and 'read' not in repr(saved)
    second, resumed_events = make_observer('resume-run')
    second.restore(saved)
    second.activate_resume([reply])
    assert resumed_events[-1].event_type == 'turn_resumed'
    assert resumed_events[-1].data.turn_id == events[1].data.turn_id
    assert resumed_events[-1].data.origin_run_id == 'run'
    second.mark_ready()
    second.observe([reply, ToolMessage(id='result', content='body', tool_call_id='call')])
    assert resumed_events[-1].data.status == 'completed'
    assert second.snapshot(pending=False) is None


def test_recovery_view_does_not_complete_and_failed_retry_gets_new_turn():
    first, events = make_observer()
    reply = AIMessage(id='ai', content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'call'}])
    result = ToolMessage(id='result', content='skipped', tool_call_id='call', status='error')
    first.begin('model-call')
    first.accept(reply)
    first.mark_ready()
    first.observe([reply, result], visibility='recovery_view')
    assert events[-1].event_type == 'turn_started'
    failure = RunFailureData('execute', 'unknown', 'unclassified', 'RuntimeError')
    first.fail(failure)
    assert events[-1].data.status == 'failed' and events[-1].data.tool_message_ids == ('result',)
    saved = json.loads(json.dumps(first.snapshot(pending=True)))
    second, retried = make_observer('retry-run')
    second.restore(saved)
    second.activate_resume([reply])
    assert retried[-1].event_type == 'turn_started'
    assert retried[-1].data.kind == 'approval_retry'
    assert retried[-1].data.previous_turn_id == events[1].data.turn_id
    assert retried[-1].data.turn_id != events[1].data.turn_id
    assert retried[-1].data.model_call_id is None


def test_restored_turn_is_not_failed_by_preparation_error():
    first, events = make_observer()
    reply = AIMessage(id='ai', content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'call'}])
    first.begin('model-call')
    first.accept(reply)
    first.observe([reply])
    first.result(interrupted=True)
    saved = first.snapshot(pending=True)
    second, events = make_observer('resume')
    second.restore(saved)
    second.fail(RunFailureData('prepare', 'unknown', 'unclassified', 'OSError'))
    assert [e.event_type for e in events] == ['run_started']
    assert second.snapshot(pending=True) == saved


def test_invalid_continuation_is_rejected_without_events():
    observer, events = make_observer()
    with pytest.raises(ValueError):
        observer.restore({'version': 1, 'turn_id': object()})
    assert len(events) == 1


def test_approval_retry_can_suspend_and_resume_without_inventing_model_call():
    first, events = make_observer()
    reply = AIMessage(id='ai', content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'call'}])
    first.begin('original-call')
    first.accept(reply)
    first.observe([reply])
    first.fail(RunFailureData('execute', 'unknown', 'unclassified', 'RuntimeError'))
    retry, retry_events = make_observer('retry')
    retry.restore(first.snapshot(pending=True))
    retry.activate_resume([reply])
    retry.observe([reply])
    retry.result(interrupted=True)
    resumed, resumed_events = make_observer('resume')
    resumed.restore(retry.snapshot(pending=True))
    resumed.activate_resume([reply])
    assert resumed_events[-1].event_type == 'turn_resumed'
    assert resumed_events[-1].data.turn_id == retry_events[1].data.turn_id
    assert resumed_events[-1].data.model_call_id is None


def test_run_observation_scope_binds_both_contexts_and_resets_after_error():
    from naiyue_test_agent.application.run_events import RunObservation
    from naiyue_test_agent.agent_runtime.events import current_run_event_publisher
    from naiyue_test_agent.agent_runtime.turn_observation import current_turn_observer

    observer, events = make_observer()
    run = RunObservation(observer.publisher, 'message')
    primary = RuntimeError('synthetic')
    with pytest.raises(RuntimeError) as caught:
        with run.bind():
            assert current_run_event_publisher() is run.publisher
            assert current_turn_observer() is run.turns
            raise primary
    assert caught.value is primary
    assert current_run_event_publisher() is None and current_turn_observer() is None


def test_missing_state_confirmation_reports_unavailable_not_completed():
    observer, events = make_observer()
    observer.begin('call')
    observer.accept(AIMessage(id='reply', content='not yet observed'))
    observer.mark_ready()
    observer.result(interrupted=False)
    assert [e.event_type for e in events] == ['run_started', 'turn_started', 'turn_observation_unavailable']
    assert events[-1].data.reason == 'incomplete_turn'
    assert events[-1].data.turn_id == events[1].data.turn_id


def test_saved_continuation_cannot_mutate_the_live_turn_or_its_restore():
    observer, events = make_observer()
    reply = AIMessage(id='reply', content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'call'}])
    observer.begin('model-call')
    observer.accept(reply)
    observer.observe([reply])
    observer.result(interrupted=True)
    saved = observer.snapshot(pending=True)
    restored, restored_events = make_observer('resume')
    restored.restore(saved)
    saved['tool_call_ids'].append('modified')
    assert observer.snapshot(pending=True)['tool_call_ids'] == ['call']
    assert restored.snapshot(pending=True)['tool_call_ids'] == ['call']
