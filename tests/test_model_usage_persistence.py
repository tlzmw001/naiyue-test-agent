"""A bounded optional writer never owns model execution or required saves."""

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest

from native_application_helpers import async_test
from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions
from test_model_usage_storage import facts


def writer(storage, subscriptions, **limits):
    from naiyue_test_agent.application.model_usage_persistence import ModelUsagePersistence
    from naiyue_test_agent.tools.settings import UsagePersistenceSettings
    return ModelUsagePersistence(storage, subscriptions.subscribe, UsagePersistenceSettings(**limits))


@async_test
async def test_writer_flushes_a_without_waiting_for_b_model_to_finish():
    saved, finished = [], []
    storage = SimpleNamespace(save_model_usage_event=saved.append,
        finish_model_usage_run=lambda *args, **kwargs: finished.append((args, kwargs)))
    subscriptions = ApplicationEventSubscriptions()
    async with writer(storage, subscriptions) as sink:
        for event in facts(rid='a') + facts(rid='b', identity='b')[:1]:
            await asyncio.to_thread(sink.accept, event)
        assert (await asyncio.wait_for(sink.aflush_run('s', 'a'), 5))['capture_incomplete'] is False
        assert len(saved) >= 2 and finished[0][0] == ('s', 'a')
        assert ('s', 'b') in sink._runs
    assert subscriptions._handlers == {} and sink._task.done()


@async_test
async def test_capacity_includes_started_io_and_overflow_preserves_accepted_facts(caplog):
    entered, release, saved, finished = Event(), Event(), [], []
    def save(event):
        entered.set(); release.wait(); saved.append(event)
    storage = SimpleNamespace(save_model_usage_event=save,
        finish_model_usage_run=lambda *args, **kwargs: finished.append(kwargs))
    subscriptions = ApplicationEventSubscriptions()
    async with writer(storage, subscriptions, max_pending_events=1) as sink:
        sink.accept(facts()[0])
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            sink.accept(facts()[1])
        finally:
            release.set()
        report = await sink.aflush_run('s', 'r')
        assert report == {'capture_incomplete': True, 'reason': 'buffer_overflow', 'dropped_events': 1}
        assert [e.event_type for e in saved] == ['model_invocation_started']
        assert finished[-1]['dropped_events'] == 1
    assert 'model_usage_buffer_overflow' in caplog.text


@async_test
async def test_storage_fault_disables_only_affected_run_and_keeps_exception_body_private(caplog):
    saved, finished = [], []
    def save(event):
        if event.run_id == 'bad':
            raise OSError('PRIVATE_DATABASE_PATH')
        saved.append(event)
    storage = SimpleNamespace(save_model_usage_event=save,
        finish_model_usage_run=lambda *args, **kwargs: finished.append((args, kwargs)))
    async with writer(storage, ApplicationEventSubscriptions()) as sink:
        for event in facts(rid='bad') + facts(rid='good', identity='good'):
            sink.accept(event)
        report = await sink.aflush_run('s', 'bad')
        assert report['capture_incomplete'] and report['reason'] == 'storage_error'
        assert not (await sink.aflush_run('s', 'good'))['capture_incomplete']
        assert len(saved) == 2 and all(e.run_id == 'good' for e in saved)
    assert 'model_usage_save_failed' in caplog.text and 'PRIVATE' not in caplog.text


@async_test
async def test_cancelled_writer_settles_started_io_and_wakes_flush_as_incomplete():
    entered, release, stored = Event(), Event(), Event()
    def save(_):
        entered.set(); release.wait(); stored.set()
    storage = SimpleNamespace(save_model_usage_event=save, finish_model_usage_run=lambda *args, **kwargs: None)
    sink = writer(storage, ApplicationEventSubscriptions())
    sink.accept(facts()[0])
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        flushing = asyncio.create_task(sink.aflush_run('s', 'r'))
        sink._task.cancel()
        # The started IO remains owned until its real completion.
        assert not sink._task.done() and not stored.is_set()
        release.set()
        report = await asyncio.wait_for(flushing, 5)
        assert stored.is_set() and report['capture_incomplete']
    finally:
        release.set()
        await sink.aclose()


@async_test
async def test_byte_overflow_does_not_enqueue_a_record_or_hide_its_gap():
    saved = []
    storage = SimpleNamespace(save_model_usage_event=saved.append, finish_model_usage_run=lambda *args, **kwargs: None)
    subscriptions = ApplicationEventSubscriptions()
    async with writer(storage, subscriptions, max_pending_bytes=1) as sink:
        for event in facts():
            sink.accept(event)
        assert await sink.aflush_run('s', 'r') == {
            'capture_incomplete': True, 'reason': 'buffer_overflow', 'dropped_events': 2}
        assert saved == [] and sink._count == sink._bytes == 0
    await sink.aclose()
    assert sink._task.done() and sink._runs == {} and subscriptions._handlers == {}
