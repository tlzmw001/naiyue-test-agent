"""Finite state confirmation does not mistake an old summary for a replacement."""

from langchain_core.messages import HumanMessage

from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
from naiyue_test_agent.agent_runtime.context_operation_observation import ContextOperationObserver


def test_only_expected_summary_and_retained_order_confirm_replacement():
    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    observer = ContextOperationObserver(pub)
    op = observer.begin_compaction('A', 'threshold', 100, 3)
    summary = HumanMessage(id='new-summary', content='new')
    kept = HumanMessage(id='kept', content='keep')
    removed = HumanMessage(id='removed', content='remove')
    observer.prepare_compaction(op, summary, [kept], [removed], 40)
    observer.observe([HumanMessage(id='old-summary', content='old', additional_kwargs={
        'naiyue_compaction_summary': True,
    }), kept])
    assert [e.event_type for e in events] == ['run_started', 'compaction_started']
    observer.observe([summary, kept])
    assert events[-1].data.status == 'applied' and events[-1].data.summary_message_id == summary.id
    observer.observe([summary, kept])
    assert len(events) == 3


def test_unconfirmed_state_is_diagnostic_not_a_fabricated_applied_result():
    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    observer = ContextOperationObserver(pub)
    op = observer.begin_compaction('A', 'threshold', 100, 3)
    observer.prepare_compaction(op, HumanMessage(content='not applied'), [], [], 20)
    observer.finalize()
    assert events[-1].data.status == 'unconfirmed' and events[-1].data.summary_message_id is None


def test_unassigned_retry_id_cannot_match_an_unassigned_human_id():
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage

    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    observer = ContextOperationObserver(pub)
    observer.model_call_started('call')
    recovery = observer.begin_recovery('A')
    compact = observer.begin_compaction('A', 'overflow', 100, 2)
    summary = HumanMessage(id='summary', content='summary')
    observer.prepare_compaction(compact, summary, [], [], 20)
    observer.retry_started(recovery)
    observer.retry_returned(recovery, SimpleNamespace(result=[AIMessage(content='not committed')]))
    observer.model_call_accepted('call')
    observer.observe([summary, HumanMessage(content='unassigned')])
    assert not any(e.event_type == 'context_recovery_finished' for e in events)
    observer.finalize()
    assert events[-1].data.status == 'unconfirmed'


def test_same_id_wrong_message_role_does_not_confirm_a_summary_replacement():
    from langchain_core.messages import AIMessage

    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    observer = ContextOperationObserver(pub)
    op = observer.begin_compaction('A', 'threshold', 100, 2)
    summary = HumanMessage(id='summary', content='expected')
    observer.prepare_compaction(op, summary, [], [], 20)
    observer.observe([AIMessage(id='summary', content='different role')])
    assert [e.event_type for e in events] == ['run_started', 'compaction_started']


def test_recovery_view_is_marked_and_retained_order_must_match():
    from langchain_core.messages import AIMessage

    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    observer = ContextOperationObserver(pub)
    op = observer.begin_compaction('A', 'threshold', 100, 4)
    summary = HumanMessage(id='summary', content='brief')
    first, second = HumanMessage(id='first', content='first'), AIMessage(id='second', content='second')
    observer.prepare_compaction(op, summary, [first, second], [HumanMessage(id='removed', content='old')], 40)
    observer.observe([summary, second, first], visibility='recovery_view')
    assert len(events) == 2
    observer.observe([summary, first, second], visibility='recovery_view')
    assert events[-1].data.status == 'applied' and events[-1].data.visibility == 'recovery_view'


def test_publisher_rejects_changed_operation_subject_without_advancing_sequence():
    from dataclasses import replace
    import pytest
    from naiyue_test_agent.agent_runtime.context_events import ContextOperationStartedData, ContextOperationFinishedData

    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    pub.publish('compaction_started', ContextOperationStartedData('op', 'compaction', 'A', 'threshold',
                                                                  before_estimate=100, original_message_count=3))
    end = ContextOperationFinishedData('op', 'compaction', 'A', 'threshold',
                                       before_estimate=100, original_message_count=3,
                                       status='skipped', reason='no_safe_cut_within_send_budget')
    with pytest.raises(ValueError):
        pub.publish('compaction_finished', replace(end, model_id='B'))
    pub.publish('compaction_finished', end)
    assert [e.sequence for e in events] == [1, 2, 3]


def test_failure_reason_never_copies_arbitrary_exception_content():
    from dataclasses import FrozenInstanceError
    import pytest

    events = []
    pub = RunEventPublisher('session', 'run', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    observer = ContextOperationObserver(pub)
    op = observer.begin_compaction('A', 'threshold', 100, 2)
    observer.finish_compaction(op, 'failed', 'PRIVATE_BODY', RuntimeError('PRIVATE_SECRET'))
    observer.flush()
    assert events[-1].data.reason == 'unclassified' and events[-1].data.error_type == 'RuntimeError'
    assert 'PRIVATE_' not in repr(events)
    with pytest.raises(FrozenInstanceError):
        events[-1].data.reason = 'changed'
