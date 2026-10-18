"""Control facts remain FIFO, immutable, and independent of input text."""

from event_sequence import CheckedPublisher
from uuid import uuid4

from langchain_core.messages import HumanMessage

from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
from naiyue_test_agent.agent_runtime.control_observation import ControlObserver


def prepare(observer, texts):
    # Input ownership moved to application code; this observer only records supplied IDs.
    messages = [HumanMessage(id=uuid4().hex, content=text) for text in texts]
    observer.record_messages(messages)
    return messages


def make_observer():
    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    return ControlObserver(pub), pub, events


def test_start_gate_and_same_text_have_distinct_control_and_message_ids():
    observer, pub, events = make_observer()
    observer.accept('steering', queue_size=0)
    observer.accept('steering', queue_size=1)
    observer.flush()
    assert events == []
    pub.publish('run_started', RunStartedData('message'))
    observer.start()
    observer.select('steering', 2)
    observer.flush()
    messages = prepare(observer, ['same', 'same'])
    assert len({m.id for m in messages}) == 2
    assert [m.content for m in messages] == ['same', 'same']
    observer.observe(messages)
    accepted = [e for e in events if e.event_type == 'control_accepted']
    delivered = [e for e in events if e.event_type == 'control_delivered']
    assert len({e.data.control_id for e in accepted}) == 2
    assert [e.data.control_id for e in accepted] == [e.data.control_id for e in delivered]
    assert [e.data.message_id for e in delivered] == [m.id for m in messages]
    observer.observe(messages)
    assert len([e for e in events if e.event_type == 'control_delivered']) == 2


def test_selected_not_visible_is_unknown_and_queued_is_discarded():
    observer, pub, events = make_observer()
    pub.publish('run_started', RunStartedData('message'))
    observer.start()
    observer.accept('steering', queue_size=0)
    observer.select('steering', 1)
    observer.accept('follow_up', queue_size=0)
    observer.close('run_failed')
    observer.flush()
    unknown = next(e for e in events if e.event_type == 'control_outcome_unknown')
    discarded = next(e for e in events if e.event_type == 'control_discarded')
    assert unknown.data.reason == 'message_state_unconfirmed'
    assert discarded.data.kind == 'follow_up' and discarded.data.reason == 'run_failed'
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))


def test_recovery_view_confirms_only_actual_human_message_id():
    observer, pub, events = make_observer()
    pub.publish('run_started', RunStartedData('message'))
    observer.start()
    observer.accept('steering', queue_size=0)
    observer.select('steering', 1)
    expected = prepare(observer, ['text'])[0]
    observer.observe([HumanMessage(id='different', content='text')])
    assert not any(e.event_type == 'control_delivered' for e in events)
    observer.observe([expected], visibility='recovery_view')
    assert events[-1].data.visibility == 'recovery_view' and events[-1].data.message_id == expected.id


def test_unprepared_old_batch_cannot_claim_delivery_of_next_batch():
    observer, pub, events = make_observer()
    pub.publish('run_started', RunStartedData('message'))
    observer.start()
    observer.accept('steering', queue_size=0)
    observer.select('steering', 1)
    observer.accept('steering', queue_size=0)
    observer.select('steering', 1)
    messages = prepare(observer, ['second'])
    observer.observe(messages)
    observer.close('run_completed')
    observer.flush()
    accepted = [e for e in events if e.event_type == 'control_accepted']
    delivered = next(e for e in events if e.event_type == 'control_delivered')
    unknown = next(e for e in events if e.event_type == 'control_outcome_unknown')
    assert delivered.data.control_id == accepted[1].data.control_id
    assert unknown.data.control_id == accepted[0].data.control_id


def test_disabled_delivery_does_not_accumulate_a_second_notification_history(monkeypatch, caplog):
    observer, pub, events = make_observer()
    pub.publish('run_started', RunStartedData('message'))
    observer.start()

    def fail_publish(*args, **kwargs):
        raise RuntimeError('PRIVATE_PUBLISH')

    monkeypatch.setattr(pub, 'publish', fail_publish)
    for _ in range(20):
        observer.accept('steering', queue_size=0)
        observer.flush()
        observer.select('steering', 1)
        messages = prepare(observer, ['same'])
        observer.observe(messages)
    assert not observer._outbox and not observer._records
    assert 'control_event_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text


def test_publisher_keeps_original_approval_subject_across_stages():
    from dataclasses import replace
    import pytest
    from naiyue_test_agent.contracts.session_events import ControlData

    events = []
    pub = CheckedPublisher('s', 'r', (events.append,))
    pub.publish('run_started', RunStartedData('approval_resume'))
    accepted = ControlData('control', 'approval', tool_call_ids=('original',),
                           decisions=('approve',), origin_run_id='origin')
    pub.publish('control_accepted', accepted)
    with pytest.raises(ValueError):
        pub.publish('control_delivery_started', replace(accepted, tool_call_ids=('other',), boundary='invocation'))
    pub.publish('control_delivery_started', replace(accepted, boundary='invocation'))
    with pytest.raises(ValueError):
        pub.publish('control_applied', replace(accepted, decisions=('reject',), boundary='approval_hook'))
    pub.publish('control_applied', replace(accepted, boundary='approval_hook'))
    assert [e.sequence for e in events] == [1, 2, 3, 4]


def test_retry_without_hitl_does_not_invent_approval_applied():
    from naiyue_test_agent.agent_runtime.control_observation import ControlObserver
    events = []
    publisher = RunEventPublisher('session', 'retry', (events.append,))
    publisher.publish('run_started', RunStartedData('approval_resume'))
    observer = ControlObserver(publisher)
    observer.accept('approval', tool_call_ids=('original',), decisions=('approve',), origin_run_id='original-run')
    observer.start()
    observer.submit_approval()
    observer.close('run_completed')
    observer.flush()
    assert [event.event_type for event in events] == [
        'run_started', 'control_accepted', 'control_delivery_started', 'control_outcome_unknown']
    assert events[-1].data.reason == 'approval_resolution_unconfirmed'
    assert events[-1].data.origin_run_id == 'original-run'
    assert events[-1].data.tool_call_ids == ('original',)
