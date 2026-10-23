"""Per-session selection preserves the original ordered publisher snapshots."""

import pytest

from native_application_helpers import async_test
from naiyue_test_agent.application.observability import run_events
from naiyue_test_agent.application.observability.run_events import ApplicationEventSubscriptions
from naiyue_test_agent.agent_runtime.events import AgentEventSource
from naiyue_test_agent.contracts.session_events import RunStartedData
from test_async_application import make_app, StreamingToolModel


def test_scope_selection_preserves_registration_order_root_children_and_logging(monkeypatch):
    calls = []
    def handler(label):
        return lambda event: calls.append((label, event.session_id, bool(event.source), event.sequence))
    monkeypatch.setattr(run_events, '_log_event', handler('log'))
    subscriptions = ApplicationEventSubscriptions()
    subscriptions.subscribe(handler('A-first'), session_id='A')
    subscriptions.subscribe(handler('global'))
    subscriptions.subscribe(handler('B'), session_id='B')
    subscriptions.subscribe(handler('A-last'), session_id='A')
    a, b = subscriptions.publisher('A', 'run-a'), subscriptions.publisher('B', 'run-b')
    a.publish('run_started', RunStartedData('message'))
    a.for_agent(AgentEventSource('root/tester/child', 'child', 'segment', 'run-a', 'delegate')).publish(
        'run_started', RunStartedData('message'))
    b.publish('run_started', RunStartedData('message'))
    assert calls == [
        (label, 'A', child, sequence) for child, sequence in ((False, 1), (True, 2))
        for label in ('log', 'A-first', 'global', 'A-last')
    ] + [(label, 'B', False, 1) for label in ('log', 'global', 'B')]


def test_unsubscribe_changes_only_future_snapshots_and_not_other_sessions():
    received = []
    subscriptions = ApplicationEventSubscriptions()
    unsub = subscriptions.subscribe(lambda e: received.append(('old', e.run_id)), session_id='A')
    subscriptions.subscribe(lambda e: received.append(('B', e.run_id)), session_id='B')
    old = subscriptions.publisher('A', 'old-run')
    unsub()
    unsub()
    subscriptions.subscribe(lambda e: received.append(('new', e.run_id)), session_id='A')
    old.publish('run_started', RunStartedData('message'))
    subscriptions.publisher('A', 'new-run').publish('run_started', RunStartedData('message'))
    subscriptions.publisher('B', 'b-run').publish('run_started', RunStartedData('message'))
    assert received == [('old', 'old-run'), ('new', 'new-run'), ('B', 'b-run')]


def test_observer_failure_is_local_to_publisher_and_preserves_other_delivery(caplog):
    received, failures = [], []
    def fail(event):
        failures.append(event.run_id)
        raise ValueError('PRIVATE_OBSERVER')
    subscriptions = ApplicationEventSubscriptions()
    subscriptions.subscribe(fail, session_id='A')
    subscriptions.subscribe(received.append)
    a = subscriptions.publisher('A', 'a-run')
    for _ in range(2):
        a.publish('run_started', RunStartedData('message'))
    subscriptions.publisher('B', 'b-run').publish('run_started', RunStartedData('message'))
    assert failures == ['a-run'] and [e.session_id for e in received] == ['A', 'A', 'B']
    assert sum(r.message.startswith('event_observer_failed') for r in caplog.records) == 1
    assert 'PRIVATE_OBSERVER' not in caplog.text


@pytest.mark.parametrize('invalid', ['', ' ', 0, True, []])
def test_subscription_requires_a_nonempty_string_scope(invalid):
    with pytest.raises(ValueError):
        ApplicationEventSubscriptions().subscribe(lambda _: None, session_id=invalid)


@async_test
async def test_application_forwards_session_scope_without_changing_global_consumers(monkeypatch):
    app = await make_app(monkeypatch, StreamingToolModel(replies=[]))
    a, b, global_events = [], [], []
    try:
        app.observability.subscribe(a.append, session_id='A')
        app.observability.subscribe(b.append, session_id='B')
        app.observability.subscribe(global_events.append)
        for sid in ('A', 'B'):
            app.sessions._sessions._events.publisher(sid, sid).publish('run_started', RunStartedData('message'))
        assert [e.session_id for e in a] == ['A']
        assert [e.session_id for e in b] == ['B']
        assert [e.session_id for e in global_events] == ['A', 'B']
    finally:
        await app.aclose()
