"""Bounded native custom reports and run-owned dispatch identity checks."""

from naiyue_test_agent import settings as application_settings

from dataclasses import asdict
import asyncio
from types import SimpleNamespace

import pytest
from langgraph.errors import GraphInterrupt

from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.tools import settings as settings_module


def runtime(writer, *, call_id='call', batch_id='batch', name='read'):
    return SimpleNamespace(tool_call_id=call_id, stream_writer=writer, context={
        '_execution_permit': {'batch_id': batch_id, 'tool_name': name}})


def request(batch='batch', call='call', name='read'):
    return SimpleNamespace(state={'tool_execution': {'batch_id': batch}},
                           tool_call={'id': call, 'name': name})


def payload(**changes):
    return {'kind': 'naiyue.tool_progress', 'version': 1, 'batch_id': 'batch',
            'tool_call_id': 'call', 'tool_name': 'read', 'activity': 'running',
            'phase': 'read', 'completed': 2, 'total': None, 'unit': 'bytes', 'text': '', **changes}


def reporter(writer, **settings):
    from naiyue_test_agent.tools.progress import ToolProgress
    return ToolProgress(runtime(writer), settings_module.ToolProgressSettings(**settings))


def observer(events, **settings):
    from naiyue_test_agent.agent_runtime.tool_execution.observation import ToolProgressObserver
    return ToolProgressObserver(RunEventPublisher('session', 'run', (events.append,)),
                                settings_module.ToolProgressSettings(**settings))


def test_reporter_throttles_by_monotonic_time_and_flushes_only_latest(monkeypatch):
    from naiyue_test_agent.tools import progress
    clock, outputs = [0.0], []
    monkeypatch.setattr(progress, 'monotonic', lambda: clock[0])
    item = reporter(outputs.append)
    item.report(phase='scan', completed=1, unit='bytes')
    item.report(phase='scan', completed=2, unit='bytes')
    item.report(phase='scan', completed=3, unit='bytes')
    assert [p['completed'] for p in outputs] == [1]
    item.flush()
    assert [p['completed'] for p in outputs] == [1, 3]
    item.flush()
    assert len(outputs) == 2
    item.report(phase='write', completed=0, unit='bytes')
    assert outputs[-1]['phase'] == 'write'
    clock[0] = 1
    item.report(phase='write', completed=4, unit='bytes')
    assert outputs[-1]['completed'] == 4
    assert all(p['batch_id'] == 'batch' and p['tool_call_id'] == 'call' for p in outputs)


def test_utf8_tail_is_a_bounded_replacement_snapshot():
    outputs = []
    item = reporter(outputs.append, max_text_bytes=5, min_interval_milliseconds=0)
    item.report(phase='output', text='first中😀')
    item.report(phase='output', text='second😀x')
    assert [p['text'] for p in outputs] == ['😀', '😀x']
    assert all(len(p['text'].encode()) <= 5 for p in outputs)


@pytest.mark.parametrize('fields', [dict(completed=True), dict(completed=-1),
    dict(completed=3, total=2), dict(total=float('inf')), dict(unit='percent'),
    dict(phase='x' * 65), dict(text=b'PRIVATE'), dict(activity='completed')])
def test_invalid_report_diagnoses_without_becoming_tool_failure(fields, caplog):
    outputs = []
    item = reporter(outputs.append)
    item.report(**{'phase': 'read', **fields})
    item.report(**{'phase': 'read', **fields})
    assert not outputs and 'tool_progress_unavailable' in caplog.text
    assert sum('tool_progress_unavailable' in r.message for r in caplog.records) == 1
    assert 'PRIVATE' not in caplog.text


def test_disabled_or_standalone_reporter_never_calls_writer():
    from naiyue_test_agent.tools.progress import ToolProgress
    outputs = []
    reporter(outputs.append, enabled=False).report(phase='read')
    ToolProgress(SimpleNamespace(context={}, tool_call_id='local')).report(phase='read')
    assert not outputs


@pytest.mark.parametrize('error', [RuntimeError('PRIVATE'), GraphInterrupt(())])
def test_writer_fault_isolated_but_framework_control_propagates(error, caplog):
    def fail(_):
        raise error
    item = reporter(fail)
    if isinstance(error, GraphInterrupt):
        with pytest.raises(GraphInterrupt) as caught:
            item.report(phase='read')
        assert caught.value is error
    else:
        item.report(phase='read')
        assert 'tool_progress_unavailable' in caplog.text and 'PRIVATE' not in caplog.text


def test_observer_rejects_unregistered_wrong_and_closed_calls_and_old_batches(caplog):
    events = []
    item = observer(events)
    item.accept_custom(payload())
    assert not events
    item.begin_dispatch(request())
    item.accept_custom(payload(text='TRANSIENT_PROGRESS'))
    assert len(events) == 1 and events[0].event_type == 'tool_progress'
    assert events[0].data.text == 'TRANSIENT_PROGRESS'
    assert (events[0].session_id, events[0].run_id, events[0].sequence) == ('session', 'run', 1)
    for fields in ({'tool_call_id': 'other'}, {'tool_name': 'other'}, {'batch_id': 'old'},
                   {'version': True}, {'text': 'x' * 8193}, {'extra': 'PRIVATE'},
                   {'completed': False}, {'total': 1}):
        item.accept_custom(payload(**fields))
    assert len(events) == 1
    item.finish('batch', 'call')
    item.accept_custom(payload())
    assert len(events) == 1
    item.begin_dispatch(request(batch='next'))
    item.accept_custom(payload())
    item.accept_custom(payload(batch_id='next'))
    assert len(events) == 2 and events[-1].data.batch_id == 'next'
    assert 'PRIVATE' not in caplog.text
    # The runtime receiver must not keep presentation text snapshots.
    assert not any(isinstance(v, dict) and 'text' in v for v in vars(item).values())
    assert 'TRANSIENT_PROGRESS' not in repr(vars(item))


def test_dispatch_progress_uses_registered_identity_and_event_data_is_validated():
    from naiyue_test_agent.agent_runtime.stream_events import ToolProgressData
    events = []
    item = observer(events)
    item.begin_dispatch(request())
    item.report_dispatch(request(), 'queued')
    item.report_dispatch(request(), 'preparing')
    assert [e.data.activity for e in events] == ['queued', 'preparing']
    assert set(asdict(events[0].data)) == set(payload()) - {'kind', 'version'}
    with pytest.raises(ValueError):
        ToolProgressData(**{**asdict(events[0].data), 'completed': True})


def test_progress_settings_are_loaded_and_invalid_budgets_fall_back(tmp_path, caplog):
    source = tmp_path / 'settings.toml'
    source.write_text('[progress]\nenabled=true\nmin_interval_milliseconds=0\nmax_text_bytes=40\n')
    loaded = application_settings.load_settings(source)
    assert loaded.progress.max_text_bytes == 40 and loaded.progress.min_interval_milliseconds == 0
    source.write_text('[progress]\nmax_text_bytes=-1\n[tools.read]\ndefault_limit=7\n')
    loaded = application_settings.load_settings(source)
    assert loaded.progress == settings_module.ToolProgressSettings() and loaded.tools.read.default_limit == 7
    assert 'tool_progress_settings_fallback' in caplog.text


def test_run_services_projects_only_the_matching_progress_observer():
    from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services
    from naiyue_test_agent.agent_runtime.invocation_context import make_invocation_context
    item = observer([])
    with bind_run_services(RunServices(publisher=item.publisher, tool_progress=item)):
        assert make_invocation_context('session', 'run', None, None, ())['_tool_progress_observer'] is item
        assert '_tool_progress_observer' not in make_invocation_context('other', 'run', None, None, ())


@pytest.mark.parametrize('error', [GraphInterrupt(()), asyncio.CancelledError()])
def test_dispatch_and_stream_adapter_preserve_control_exception_identity(monkeypatch, error):
    from naiyue_test_agent.agent_runtime.tool_execution.observation import observe_tool_progress
    item = observer([])
    def fail(_):
        raise error
    monkeypatch.setattr(item, 'accept_custom', fail)
    with pytest.raises(type(error)) as caught:
        observe_tool_progress({'_tool_progress_observer': item}, 'accept_custom', payload())
    assert caught.value is error


def test_async_receiver_ignores_child_namespace_and_unrelated_custom_payloads():
    from naiyue_test_agent.agent_runtime.async_message_observation import _StreamState
    events = []
    item = observer(events)
    item.begin_dispatch(request())
    state = _StreamState(item.publisher, {'_tool_progress_observer': item})
    state.part({'ns': ('child',), 'type': 'custom', 'data': payload()})
    state.part({'ns': (), 'type': 'custom', 'data': {'kind': 'unrelated'}})
    assert not events
    state.part({'ns': (), 'type': 'custom', 'data': payload()})
    assert len(events) == 1 and events[0].data.phase == 'read'


@pytest.mark.parametrize('failure', [GraphInterrupt(()), asyncio.CancelledError()])
def test_control_writer_is_not_retried_during_tool_cleanup(failure):
    calls = []
    def write(payload):
        calls.append(payload['phase'])
        raise failure
    item = reporter(write)
    with pytest.raises(type(failure)) as caught:
        item.report(phase='working')
    assert caught.value is failure
    # Cleanup must preserve the original control and not call a failed writer.
    item.report(phase='cleanup', activity='settling')
    item.flush()
    assert calls == ['working']
