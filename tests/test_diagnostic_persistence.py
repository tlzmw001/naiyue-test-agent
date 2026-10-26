"""Optional diagnostic IO must preserve execution and bounded writer ownership."""

import asyncio
from threading import Event

from native_application_helpers import async_test
from naiyue_test_agent.application.observability.run_events import ApplicationEventSubscriptions
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.agent_runtime.events import RuntimeEvent, RunStartedData, RunFinishedData
from test_diagnostic_storage import limits
from test_model_usage_storage import seed


def publisher_event(kind='run_started', *, rid='r', sequence=1):
    from datetime import datetime, timezone
    return RuntimeEvent(kind, 's', rid, sequence, datetime.now(timezone.utc).isoformat(),
        RunFinishedData('completed') if kind == 'run_finished' else RunStartedData('message'))


@async_test
async def test_writer_overflow_counts_inflight_but_retains_finite_terminal_summary(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
    entered, release = Event(), Event()
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        original = store.diagnostics.save_diagnostic_event
        def blocked(*args):
            entered.set()
            release.wait()
            return original(*args)
        monkeypatch.setattr(store.diagnostics, 'save_diagnostic_event', blocked)
        subscriptions = ApplicationEventSubscriptions()
        async with DiagnosticPersistence(store, subscriptions.subscribe, limits(max_pending_events=1)) as sink:
            sink.accept(publisher_event())
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                sink.accept(publisher_event(sequence=2))
                sink.accept(publisher_event('run_finished', sequence=3))
            finally:
                release.set()
        summary = store.diagnostics.read_diagnostic_events('s', 'r')['summary']
        assert summary['capture_incomplete'] and summary['reason'] == 'buffer_overflow'
        assert summary['dropped_events'] == 1 and summary['status'] == 'completed'
        assert subscriptions._handlers == {} and sink._task.done() and not sink._runs
    assert 'diagnostic_buffer_overflow' in caplog.text


@async_test
async def test_storage_error_affects_only_its_root_and_never_logs_error_body(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        original = store.diagnostics.save_diagnostic_event
        def fail_bad(record, settings):
            if record['run_id'] == 'bad':
                raise OSError('PRIVATE_DIAGNOSTIC_ERROR')
            return original(record, settings)
        monkeypatch.setattr(store.diagnostics, 'save_diagnostic_event', fail_bad)
        async with DiagnosticPersistence(store, ApplicationEventSubscriptions().subscribe, limits()) as sink:
            for rid in ('bad', 'good'):
                sink.accept(publisher_event(rid=rid))
                sink.accept(publisher_event('run_finished', rid=rid, sequence=2))
        bad = store.diagnostics.read_diagnostic_events('s', 'bad')['summary']
        good = store.diagnostics.read_diagnostic_events('s', 'good')['summary']
        assert bad['capture_incomplete'] and bad['status'] == 'completed'
        assert not good['capture_incomplete'] and good['status'] == 'completed'
    assert 'diagnostic_save_failed' in caplog.text and 'PRIVATE_DIAGNOSTIC_ERROR' not in caplog.text


@async_test
async def test_missing_terminal_and_startup_cleanup_failure_are_observable_only(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        def fail(*args):
            raise OSError('PRIVATE_CLEANUP')
        monkeypatch.setattr(store.diagnostics, 'prepare_diagnostics', fail)
        async with DiagnosticPersistence(store, ApplicationEventSubscriptions().subscribe, limits()) as sink:
            sink.accept(publisher_event())
        summary = store.diagnostics.read_diagnostic_events('s', 'r')['summary']
        assert summary['status'] is None and summary['capture_state'] == 'interrupted'
        assert summary['capture_incomplete']
    assert 'diagnostic_prepare_failed' in caplog.text and 'PRIVATE_CLEANUP' not in caplog.text


@async_test
async def test_projection_failure_marks_existing_root_capture_gap(tmp_path, caplog):
    from dataclasses import replace
    from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        async with DiagnosticPersistence(store, ApplicationEventSubscriptions().subscribe, limits()) as sink:
            sink.accept(publisher_event())
            sink.accept(replace(publisher_event(), sequence=0))
            sink.accept(publisher_event('run_finished', sequence=3))
        summary = store.diagnostics.read_diagnostic_events('s', 'r')['summary']
        assert summary['status'] == 'completed'
        assert summary['capture_incomplete'] and summary['reason'] == 'invalid_event'
        assert summary['dropped_events'] == 1
    assert 'diagnostic_projection_failed' in caplog.text


@async_test
async def test_byte_overflow_and_root_state_capacity_are_independently_bounded(tmp_path, caplog):
    from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        async with DiagnosticPersistence(store, ApplicationEventSubscriptions().subscribe,
                                         limits(max_pending_bytes=1, max_pending_events=1)) as sink:
            sink.accept(publisher_event())
            sink.accept(publisher_event(rid='untracked'))
            assert sink._bytes <= 1 and len(sink._runs) == 1
            sink.accept(publisher_event('run_finished', sequence=3))
        summary = store.diagnostics.read_diagnostic_events('s', 'r')['summary']
        assert summary['status'] == 'completed' and summary['reason'] == 'buffer_overflow'
        assert summary['capture_incomplete'] and summary['dropped_events'] == 1
        assert len(store.diagnostics.read_diagnostic_runs('s')['runs']) == 1
    assert 'diagnostic_run_capacity' in caplog.text


@async_test
async def test_worker_cancellation_settles_inflight_io_and_reports_incomplete(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
    entered, release, settled = Event(), Event(), Event()
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        original = store.diagnostics.save_diagnostic_event
        def blocked(*args):
            entered.set()
            release.wait()
            result = original(*args)
            settled.set()
            return result
        monkeypatch.setattr(store.diagnostics, 'save_diagnostic_event', blocked)
        async with DiagnosticPersistence(store, ApplicationEventSubscriptions().subscribe, limits()) as sink:
            sink.accept(publisher_event())
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                sink._task.cancel()
            finally:
                release.set()
        assert settled.is_set() and sink._task.cancelled()
        summary = store.diagnostics.read_diagnostic_events('s', 'r')['summary']
        assert summary['capture_incomplete'] and summary['reason'] == 'writer_unavailable'
        assert summary['status'] is None and not sink._runs
    assert 'diagnostic_writer_failed' in caplog.text


@async_test
async def test_root_capacity_includes_summary_currently_being_written(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application.observability.diagnostic_persistence import DiagnosticPersistence
    entered, release = Event(), Event()
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        original = store.diagnostics.finish_diagnostic_run
        def blocked(*args, **kwargs):
            entered.set()
            release.wait()
            return original(*args, **kwargs)
        monkeypatch.setattr(store.diagnostics, 'finish_diagnostic_run', blocked)
        async with DiagnosticPersistence(store, ApplicationEventSubscriptions().subscribe,
                                         limits(max_pending_events=1)) as sink:
            sink.accept(publisher_event())
            sink.accept(publisher_event('run_finished', sequence=2))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert len(sink._runs) == 1
                sink.accept(publisher_event(rid='another'))
                assert len(sink._runs) == 1
            finally:
                release.set()
        assert [r['run_id'] for r in store.diagnostics.read_diagnostic_runs('s')['runs']] == ['r']
    assert 'diagnostic_run_capacity' in caplog.text
