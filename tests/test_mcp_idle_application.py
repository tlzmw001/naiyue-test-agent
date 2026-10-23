"""Formal graph occupancy through models, approvals, persistence and SDK IO."""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import stdio_settings, process_alive, http_server, facts
from test_mcp_idle_reclamation import clock, expire, settled
from test_mcp_lifecycle import reached
from test_mcp_subagents import make_child_app, MCPChildModel
from test_mcp_runtime_configuration import write_config


def server(path):
    return stdio_settings(path, 'stateful', allowed_tools=['echo'], idle_timeout_seconds=10)


@pytest.mark.parametrize('persistent', [False, True])
def test_root_pending_approval_keeps_connection_then_rebuilds_after_full_idle(monkeypatch, tmp_path, persistent):
    async def scenario():
        path = tmp_path / 'approval.jsonl'
        replies = [call(NAME, {}, 'one'), AIMessage(content='one'), call(NAME, {}, 'two'),
                   AIMessage(content='two'), call(NAME, {}, 'three'), AIMessage(content='three')]
        options = AppSettings.model_validate({'mcp': {'servers': {'local': server(path).model_dump()}}})
        if persistent:
            model = StreamingToolModel(replies=replies)
            monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
            context = open_async_persistent_application('unused', database_path=tmp_path / 'state.sqlite',
                tools=[], initial_active_tools=[NAME], system_prompt='', response_format=None, settings=options)
        else:
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def opened():
                app, _ = await make_mcp_app(monkeypatch, path, replies, server=server(path))
                try: yield app
                finally: await app.aclose()
            context = opened()
        async with context as app:
            resources = app.sessions._sessions.mcp_resources
            now = clock(resources)
            sid = await app.sessions.create('A')
            await app.runs.start(sid, [('user', 'one')])
            await app.runs.resume_approval(sid, ['approve'])
            pid = calls(path)[0]['pid']
            assert resources._idle_handles and sid not in resources._active_sessions
            result = await app.runs.start(sid, [('user', 'two')])
            assert result['approval_requests'] and sid in resources._active_sessions
            now[0] = 1000
            expire(resources)
            assert not resources._idle_handles and process_alive(pid)
            await app.runs.resume_approval(sid, ['reject'])
            assert next(iter(resources._idle_handles.values()))[2] == 1010
            now[0] = 1010
            expire(resources)
            await settled(resources)
            assert not process_alive(pid)
            await app.runs.start(sid, [('user', 'three')])
            await app.runs.resume_approval(sid, ['approve'])
            assert [r['count'] for r in calls(path)] == [1, 1] and calls(path)[-1]['pid'] != pid
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['model', 'rpc'])
def test_active_run_protects_connection_until_model_and_rpc_finish(monkeypatch, tmp_path, phase):
    async def scenario():
        path = tmp_path / 'active.jsonl'
        app, model = await make_mcp_app(monkeypatch, path,
            [call(NAME, {'text': 'wait' if phase == 'rpc' else 'one'}), AIMessage(content='done')],
            server=server(path), mode='BypassApprovals')
        resources = app.sessions._sessions.mcp_resources
        now = clock(resources)
        entered, release = asyncio.Event(), asyncio.Event()
        original = StreamingToolModel._next
        async def next_response(self, messages):
            if self is model and len(self.seen) == 1 and phase == 'model':
                entered.set()
                await release.wait()
            return await original(self, messages)
        monkeypatch.setattr(StreamingToolModel, '_next', next_response)
        sid = await app.sessions.create('A')
        run = asyncio.create_task(app.runs.start(sid, [('user', 'hold')]))
        try:
            if phase == 'model': await entered.wait()
            else: await reached(lambda: bool(calls(path)))
            pid = calls(path)[0]['pid']
            now[0] = 1000
            expire(resources)
            assert process_alive(pid) and not resources._idle_handles and sid in resources._active_sessions
            release.set()
            path.with_suffix('.release').touch()
            await run
            assert next(iter(resources._idle_handles.values()))[2] == 1010
            now[0] = 1010
            expire(resources)
            await settled(resources)
            assert not process_alive(pid) and len(calls(path)) == 1
        finally:
            release.set()
            path.with_suffix('.release').touch()
            await app.aclose()
            await asyncio.gather(run, return_exceptions=True)
    asyncio.run(scenario())


def test_required_persistence_finish_remains_occupied(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'save.jsonl'
        model = StreamingToolModel(replies=[call(NAME, {}), AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        settings = AppSettings.model_validate({'permissions': {'mode': 'BypassApprovals'},
            'mcp': {'servers': {'local': server(path).model_dump()}}})
        async with open_async_persistent_application('unused', database_path=tmp_path / 'state.sqlite',
                tools=[], initial_active_tools=[NAME], system_prompt='', response_format=None, settings=settings) as app:
            resources = app.sessions._sessions.mcp_resources
            now = clock(resources)
            entered, release = asyncio.Event(), asyncio.Event()
            persistence = app.sessions._sessions._persistence
            original = persistence.afinish
            async def finishing(*args, **kwargs):
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)
            monkeypatch.setattr(persistence, 'afinish', finishing)
            sid = await app.sessions.create('A')
            task = asyncio.create_task(app.runs.start(sid, [('user', 'save')]))
            try:
                await entered.wait()
                now[0] = 1000
                expire(resources)
                assert sid in resources._active_sessions and process_alive(calls(path)[0]['pid'])
                release.set()
                await task
                assert next(iter(resources._idle_handles.values()))[2] == 1010
            finally:
                release.set()
                await task
    asyncio.run(scenario())


def test_live_child_approval_keeps_owner_until_child_final_release(monkeypatch, tmp_path):
    from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
    async def scenario():
        path = tmp_path / 'child.jsonl'
        child = MCPChildModel(replies=[call(NAME, {}, 'a'), call(NAME, {}, 'b'), AIMessage(content='child done')])
        app, _ = await make_child_app(monkeypatch, path, child, server=server(path))
        resources = app.sessions._sessions.mcp_resources
        now = clock(resources)
        queue = approval_notifications(monkeypatch, app)
        sid = await app.sessions.create('A')
        task = asyncio.create_task(app.runs.start(sid, [('user', 'child')]))
        try:
            first = await next_approval(app, sid, queue)
            await submit(app, sid, first)
            second = await next_approval(app, sid, queue)
            pid = calls(path)[0]['pid']
            now[0] = 1000
            expire(resources)
            assert process_alive(pid) and sid in resources._active_sessions and not resources._idle_handles
            await submit(app, sid, second)
            await task
            assert [r['count'] for r in calls(path)] == [1, 2]
            assert calls(path)[1]['pid'] == pid and not process_alive(pid)
            assert not resources._owners and not resources._idle_handles
        finally:
            await cancel_run(task)
            await app.aclose()
    asyncio.run(scenario())


def test_runtime_disable_reenable_and_ttl_change_do_not_resurrect_disabled_owner(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'refresh.jsonl', tmp_path / 'settings.toml'
        selected = server(path)
        app, _ = await make_mcp_app(monkeypatch, path,
            [call(NAME, {}), AIMessage(content='one'), call(NAME, {}), AIMessage(content='two')],
            server=selected, mode='BypassApprovals')
        resources = app.sessions._sessions.mcp_resources
        now = clock(resources)
        try:
            sid = await app.sessions.create('A')
            await app.runs.start(sid, [('user', 'one')])
            old_key, (old_owner, _, deadline) = next(iter(resources._idle_handles.items()))
            changed = selected.model_copy(update={'idle_timeout_seconds': 20})
            write_config(config, {'local': changed})
            report = await app.mcp.refresh(config)
            assert report['servers'][0]['error'] == 'connection_configuration_changed'
            assert next(iter(resources._idle_handles.values()))[2] == deadline
            write_config(config, {'local': selected.model_copy(update={'enabled': False})})
            await app.mcp.refresh(config)
            assert not resources._idle_handles and not process_alive(calls(path)[0]['pid'])
            now[0] = 1000
            resources._expire_idle(old_key, old_owner, deadline)
            assert not resources._owners
            write_config(config, {'local': changed})
            await app.mcp.refresh(config)
            await app.runs.start(sid, [('user', 'two')])
            resources._expire_idle(old_key, old_owner, deadline)
            assert next(iter(resources._idle_handles.values()))[2] == 1020
            assert [r['count'] for r in calls(path)] == [1, 1]
        finally:
            await app.aclose()
    asyncio.run(scenario())
