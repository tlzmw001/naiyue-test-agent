"""Run metadata stays immutable, ordered, isolated, and safe to observe."""

from runtime_context_helpers import typed_scope
from event_sequence import CheckedPublisher

from dataclasses import FrozenInstanceError
from datetime import datetime
from threading import Thread

import pytest

from naiyue_test_agent.agent_runtime.events import (
    RunEventPublisher, RunFailureData, RunFinishedData, RunStartedData,
)
from naiyue_test_agent.application.observability.run_events import ApplicationEventSubscriptions


def test_publisher_reports_immutable_ordered_metadata():
    received = []
    publisher = CheckedPublisher('session', 'run', (received.append,))
    publisher.publish('run_started', RunStartedData('message'))
    publisher.publish('run_finished', RunFinishedData('completed'))
    assert [(e.event_type, e.sequence) for e in received] == [
        ('run_started', 1), ('run_finished', 2),
    ]
    assert all(e.session_id == 'session' and e.run_id == 'run' for e in received)
    assert datetime.fromisoformat(received[0].timestamp).utcoffset().total_seconds() == 0
    with pytest.raises(FrozenInstanceError):
        received[0].run_id = 'forged'
    with pytest.raises(FrozenInstanceError):
        received[1].data.status = 'failed'
    with pytest.raises(ValueError):
        publisher.publish('run_finished', RunFinishedData('completed'))
    assert len(received) == 2


def test_wrong_lifecycle_does_not_publish_a_fake_event():
    received = []
    publisher = CheckedPublisher('session', 'run', (received.append,))
    with pytest.raises(ValueError):
        publisher.publish('run_finished', RunFinishedData('completed'))
    with pytest.raises(ValueError):
        publisher.publish('run_started', RunFinishedData('completed'))
    assert received == []


def test_faulty_observer_is_isolated_per_run_and_not_exposed_in_logs(caplog):
    received, calls = [], []

    def faulty(event):
        calls.append(event.run_id)
        raise RuntimeError('SENSITIVE_OBSERVER_ERROR')

    subscriptions = ApplicationEventSubscriptions()
    subscriptions.subscribe(faulty)
    subscriptions.subscribe(received.append)
    with caplog.at_level('INFO'):
        for run_id in ('one', 'two'):
            publisher = subscriptions.publisher('session', run_id)
            publisher.publish('run_started', RunStartedData('message'))
            publisher.publish('run_finished', RunFinishedData('completed'))
    assert calls == ['one', 'two']
    assert [(e.run_id, e.sequence) for e in received] == [
        ('one', 1), ('one', 2), ('two', 1), ('two', 2),
    ]
    assert 'SENSITIVE_OBSERVER_ERROR' not in caplog.text
    logs = [r.application_event for r in caplog.records if hasattr(r, 'application_event')]
    assert len(logs) == 4 and logs[-1]['status'] == 'completed'
    assert all('thread_id' not in event for event in logs)


def test_subscription_snapshot_and_idempotent_unsubscribe():
    first, second = [], []
    subscriptions = ApplicationEventSubscriptions()
    unsubscribe = subscriptions.subscribe(first.append)
    old = subscriptions.publisher('session', 'old')
    subscriptions.subscribe(second.append)
    unsubscribe()
    unsubscribe()
    old.publish('run_started', RunStartedData('message'))
    old.publish('run_finished', RunFinishedData('completed'))
    new = subscriptions.publisher('session', 'new')
    new.publish('run_started', RunStartedData('message'))
    new.publish('run_finished', RunFinishedData('completed'))
    assert [e.run_id for e in first] == ['old', 'old']
    assert [e.run_id for e in second] == ['new', 'new']


def test_async_callbacks_are_rejected_without_running_them(caplog):
    subscriptions = ApplicationEventSubscriptions()

    async def async_handler(_event):
        pass

    class AsyncHandler:
        async def __call__(self, _event):
            pass

    for handler in (async_handler, AsyncHandler(), None):
        with pytest.raises(TypeError):
            subscriptions.subscribe(handler)

    calls = []

    def disguised(event):
        calls.append(event)
        return async_handler(event)

    subscriptions.subscribe(disguised)
    publisher = subscriptions.publisher('session', 'run')
    publisher.publish('run_started', RunStartedData('message'))
    publisher.publish('run_finished', RunFinishedData('completed'))
    assert len(calls) == 1
    assert 'event_observer_failed' in caplog.text


def test_separate_runs_and_applications_do_not_share_observers():
    first, second = [], []
    subscriptions = ApplicationEventSubscriptions()
    other = ApplicationEventSubscriptions()
    subscriptions.subscribe(first.append)
    other.subscribe(second.append)

    def publish(index):
        publisher = subscriptions.publisher(f'session-{index}', f'run-{index}')
        publisher.publish('run_started', RunStartedData('message'))
        publisher.publish('run_finished', RunFinishedData('completed'))

    threads = [Thread(target=publish, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert second == []
    for i in range(2):
        assert [(e.session_id, e.sequence) for e in first if e.run_id == f'run-{i}'] == [
            (f'session-{i}', 1), (f'session-{i}', 2),
        ]


@pytest.mark.parametrize('fields', [
    ('forged', 'unknown', 'unclassified', 'RuntimeError'),
    ('execute', {'secret': 'mutable'}, 'unclassified', 'RuntimeError'),
    ('execute', 'unknown', 'PRIVATE ERROR TEXT', 'RuntimeError'),
    ('execute', 'unknown', 'unclassified', 'Invalid\nType'),
])
def test_failure_payload_cannot_hide_mutable_or_raw_error_content(fields):
    with pytest.raises(ValueError):
        RunFailureData(*fields)


def test_call_pairing_and_payload_identity_are_enforced():
    from naiyue_test_agent.agent_runtime.events import ModelInvocationStartedData, ModelInvocationFinishedData

    received = []
    publisher = CheckedPublisher('session', 'run', (received.append,))
    publisher.publish('run_started', RunStartedData('message'))
    publisher.publish('model_call_started', ModelInvocationStartedData('call', 'A', 'agent'))
    with pytest.raises(ValueError):
        publisher.publish('model_call_started', ModelInvocationStartedData('call', 'A', 'agent'))
    with pytest.raises(ValueError):
        publisher.publish('model_call_finished', ModelInvocationFinishedData('other', 'returned'))
    publisher.publish('model_call_finished', ModelInvocationFinishedData('call', 'returned'))
    with pytest.raises(ValueError):
        publisher.publish('model_call_finished', ModelInvocationFinishedData('call', 'returned'))
    publisher.publish('run_finished', RunFinishedData('completed'))
    assert [e.sequence for e in received] == [1, 2, 3, 4]


def test_nested_run_service_bindings_restore_parent_and_reset():
    from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope, current_run_scope

    parent = RunEventPublisher('parent', 'parent-run', ())
    child = RunEventPublisher('child', 'child-run', ())
    assert current_run_scope() is None
    with bind_run_scope(typed_scope(publisher=parent)):
        assert current_run_scope().observation.publisher is parent
        with pytest.raises(RuntimeError):
            with bind_run_scope(typed_scope(publisher=child)):
                assert current_run_scope().observation.publisher is child
                raise RuntimeError('synthetic')
        assert current_run_scope().observation.publisher is parent
    assert current_run_scope() is None


@pytest.mark.parametrize('kind', ['model_start', 'tool_start', 'model_finish', 'tool_finish'])
def test_call_payload_rejects_mutable_subjects(kind):
    from naiyue_test_agent.agent_runtime.events import (
        ModelInvocationStartedData, ModelInvocationFinishedData, ToolCallStartedData, ToolCallFinishedData,
    )
    with pytest.raises(ValueError):
        if kind == 'model_start':
            ModelInvocationStartedData('call', {'mutable': True}, 'agent')
        elif kind == 'tool_start':
            ToolCallStartedData('call', ['mutable'])
        elif kind == 'model_finish':
            ModelInvocationFinishedData('call', {'mutable': True})
        else:
            ToolCallFinishedData('call', ['mutable'], 'dispatch', 'success')
