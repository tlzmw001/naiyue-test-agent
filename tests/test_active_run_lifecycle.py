"""Run identity, occupancy and scoped application teardown."""

import asyncio
from dataclasses import fields

import pytest

from native_application_helpers import async_test
from naiyue_test_agent.application.errors import SessionBusyError
from naiyue_test_agent.application.sessions.session_state import SessionState
from test_agent_session import make_service
from test_application_run_events import make_app


def test_state_contains_only_session_facts_and_run_has_independent_defaults():
    from naiyue_test_agent.application.sessions.session_state import ActiveRun
    assert {field.name for field in fields(SessionState)} == {
        'thread_id', 'model_id', 'project_id', 'execution_root', 'active_tool_names', 'permission_mode',
        'thinking_level', 'session_allow', 'pending_tool_approval', 'pending_turn',
        'manual_review_required', 'conversation_revision'}
    a, b = ActiveRun('same', None), ActiveRun('same', None)
    from naiyue_test_agent.contracts.replies import QueuedInput
    a.steering_queue.append(QueuedInput('m', 'first', 'steering'))
    a.subagent_instances['child'] = {}
    assert not b.steering_queue and not b.subagent_instances


def test_close_retains_occupancy_and_stale_same_id_cannot_release_new_run():
    service = make_service()
    sid = service.create_session('A')
    owner, old = service._admit(sid, 'same', 'message')
    owner.close_control_window(old)
    assert owner.is_busy() and owner._run is old and not old.controls_open
    with pytest.raises(SessionBusyError):
        service._admit(sid, 'other', 'message')
    owner.release_run(old)
    _, current = service._admit(sid, 'same', 'message')
    with pytest.raises(SessionBusyError):
        owner.release_run(old)
    with pytest.raises(SessionBusyError):
        owner.close_control_window(old)
    assert owner._run is current
    owner.close_control_window(current)
    owner.release_run(current)


def test_release_keeps_unsettled_child_handles():
    service = make_service()
    sid = service.create_session('A')
    owner, run = service._admit(sid, 'parent', 'message')
    child = {'driver': object(), 'settled': False}
    run.subagent_instances['root/tester/child'] = child
    owner.close_control_window(run)
    with pytest.raises(SessionBusyError, match='not settled'):
        owner.release_run(run)
    assert owner._run is run and owner.is_busy()
    assert run.subagent_instances['root/tester/child'] is child


@async_test
async def test_close_waits_scoped_operation_not_callers_later_work(monkeypatch):
    app = await make_app(monkeypatch)
    entered, release, unrelated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    original = app.models._catalog.aclose
    async def close_catalog():
        calls.append('catalog')
        await original()
    monkeypatch.setattr(app.models._catalog, 'aclose', close_catalog)
    async def caller():
        with app._lifetime.operation():
            entered.set()
            await release.wait()
            # Already accepted work may finish nested operations during close.
            with app._lifetime.operation():
                assert app.tools.list() is not None
        await unrelated.wait()
    task = asyncio.create_task(caller())
    await entered.wait()
    closing = asyncio.create_task(app.aclose())
    await asyncio.sleep(0)
    assert app._lifetime._closing and calls == [] and not closing.done()
    with pytest.raises(RuntimeError):
        await app.sessions.create('A')
    release.set()
    try:
        await asyncio.wait_for(closing, 5)
        assert calls == ['catalog'] and not task.done()
    finally:
        unrelated.set()
        await task


@async_test
async def test_concurrent_close_shares_protected_teardown(monkeypatch):
    app = await make_app(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    original = app.models._catalog.aclose
    async def close_catalog():
        calls.append('catalog')
        entered.set()
        await release.wait()
        await original()
    monkeypatch.setattr(app.models._catalog, 'aclose', close_catalog)
    first = asyncio.create_task(app.aclose())
    await entered.wait()
    second = asyncio.create_task(app.aclose())
    await asyncio.sleep(0)
    assert not second.done()
    first.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert calls == ['catalog'] and app._lifetime._closed


def test_persistence_binding_is_startup_only():
    service = make_service()
    persistence = object()
    service.bind_persistence(persistence)
    with pytest.raises(RuntimeError, match='bound once'):
        service.bind_persistence(persistence)
    loaded = make_service()
    loaded.create_session('A')
    with pytest.raises(RuntimeError, match='before sessions'):
        loaded.bind_persistence(persistence)
    assembled = make_service()
    assembled.assemble_runtime(None, system_prompt='', response_format=None)
    with pytest.raises(RuntimeError, match='before sessions'):
        assembled.bind_persistence(persistence)


@pytest.mark.parametrize('operation', ['create', 'configure', 'restore', 'prepare', 'delete'])
@pytest.mark.parametrize('fails', [False, True])
@async_test
async def test_close_drains_actual_shared_io(monkeypatch, tmp_path, operation, fails):
    from threading import Event
    from langchain_core.messages import AIMessage
    from test_async_persistent import factory, StreamingToolModel
    entered, release = asyncio.Event(), Event()
    loop, order = asyncio.get_running_loop(), []
    failure = OSError('synthetic IO failure')
    async with factory(monkeypatch, tmp_path / 'sessions.sqlite',
                       StreamingToolModel(replies=[AIMessage(content='saved')])) as app:
        sid = await app.sessions.create('A')
        await app.runs.start(sid, [('user', 'save')])
        if operation in ('restore', 'prepare'):
            app.sessions._sessions.remove_session(sid)
        name = {'create': 'create', 'configure': 'select_permission_mode',
                'restore': 'get', 'prepare': 'get', 'delete': 'delete'}[operation]
        repository = app.sessions._persistence.storage.runs if operation == 'delete' else app.sessions._persistence.storage.sessions
        original, close_catalog = getattr(repository, name), app.models._catalog.aclose
        def blocked(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            order.append('io')
            if fails:
                raise failure
            return original(*args, **kwargs)
        async def close():
            order.append('catalog')
            await close_catalog()
        monkeypatch.setattr(repository, name, blocked)
        monkeypatch.setattr(app.models._catalog, 'aclose', close)
        calls = {'create': lambda: app.sessions.create('A'),
                 'configure': lambda: app.sessions.select_permission_mode(sid, 'NoAsk'),
                 'restore': lambda: app.sessions.restore(sid),
                 'prepare': lambda: app.sessions.prepare(sid),
                 'delete': lambda: app.sessions.delete(sid)}
        task = asyncio.create_task(calls[operation]())
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closing = asyncio.create_task(app.aclose())
            await asyncio.sleep(0)
            assert order == [] and not closing.done()
            release.set()
            result, = await asyncio.gather(task, return_exceptions=True)
            await asyncio.wait_for(closing, 5)
            assert order[-1] == 'catalog' and order.count('catalog') == 1
            assert isinstance(result, BaseException) is fails
            if fails:
                assert result is failure or result.__cause__ is failure
        finally:
            release.set()
            await asyncio.gather(task, *([closing] if closing else []), return_exceptions=True)


@async_test
async def test_active_operation_cannot_wait_for_its_own_close(monkeypatch):
    app = await make_app(monkeypatch)
    with app._lifetime.operation():
        with pytest.raises(RuntimeError, match='active operation'):
            await app.aclose()
    assert not app._lifetime._closing
    await app.aclose()


@async_test
async def test_close_survives_create_rollback_after_stop_snapshot(monkeypatch, tmp_path):
    from threading import Event
    from test_async_persistent import factory, StreamingToolModel
    entered, release, removed = asyncio.Event(), Event(), Event()
    loop, closes = asyncio.get_running_loop(), []
    failure = OSError('synthetic creation failure')
    async with factory(monkeypatch, tmp_path / 'sessions.sqlite', StreamingToolModel(replies=[])) as app:
        sid = await app.sessions.create('A')
        service, stable = app.sessions._sessions, app.sessions._sessions._sessions[sid]
        create, stop, close_catalog = service.create_session, stable.request_scope_stop, app.models._catalog.aclose
        def fail_create(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            raise failure
        def creating(*args):
            try:
                return create(*args)
            finally:
                removed.set()
        def stop_after_rollback(**kwargs):
            release.set()
            assert removed.wait(5)
            return stop(**kwargs)
        async def close():
            closes.append(True)
            await close_catalog()
        monkeypatch.setattr(app.sessions._persistence.storage.sessions, 'create', fail_create)
        monkeypatch.setattr(service, 'create_session', creating)
        monkeypatch.setattr(stable, 'request_scope_stop', stop_after_rollback)
        monkeypatch.setattr(app.models._catalog, 'aclose', close)
        task = asyncio.create_task(app.sessions.create('A'))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.aclose()
            result, = await asyncio.gather(task, return_exceptions=True)
            assert result is failure and closes == [True]
            assert service.loaded_session_ids() == (sid,) and app._lifetime._operations == {}
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
