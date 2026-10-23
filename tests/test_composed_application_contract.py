"""One application type owns typed domain APIs and both storage profiles."""
import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel
from test_async_persistent import factory


@pytest.mark.parametrize('disk', [False, True])
def test_one_application_type_exposes_domains_and_no_legacy_facade(monkeypatch, tmp_path, disk):
    async def scenario():
        from naiyue_test_agent.application.core import Application
        from naiyue_test_agent.application.async_application import build_async_local_application
        from naiyue_test_agent import application
        from naiyue_test_agent.model_access import ModelCatalog
        model = StreamingToolModel(replies=[AIMessage(content='old'), AIMessage(content='new')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        if disk:
            manager = factory(monkeypatch, tmp_path / 'state.sqlite', model)
            app = await manager.__aenter__()
        else:
            app = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None)
        try:
            assert type(app) is Application and Application.__bases__ == (object,)
            assert not any(hasattr(app, old) for old in ('arun', 'aget_chat_page', 'acreate_session', 'alist_sessions'))
            assert all(getattr(app, name) is not None for name in ('sessions','runs','history','tasks','models','tools','subagents','mcp','observability'))
            sid = await app.sessions.create('A')
            await app.runs.start(sid, [('user', 'task')])
            saved = await app.sessions.status(sid)
            result = await app.runs.regenerate(sid, request_id=str(uuid4()),
                expected_reply_id=saved['last_reply_id'], expected_conversation_revision=0)
            assert result['messages'][-1].content == 'new'
            assert len((await app.history.page(sid, limit=20, body_bytes=20000, preview_chars=100))['records']) == 2
            assert app.models.default_id == 'A' and app.models.ids == ('A',)
        finally:
            if disk:
                await manager.__aexit__(None, None, None)
            else:
                await app.aclose()
    asyncio.run(scenario())


def test_cli_has_one_owner_loop_dispatch_and_no_domain_mirrors(monkeypatch):
    from naiyue_test_agent import application
    from naiyue_test_agent.cli.cli_async import AsyncCliApplication
    from naiyue_test_agent.model_access import ModelCatalog
    model = StreamingToolModel(replies=[AIMessage(content='answer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    bridge = AsyncCliApplication('unused', tools=[], system_prompt='', response_format=None)
    try:
        assert not hasattr(bridge, 'run') and not hasattr(bridge, 'get_model_usage')
        async def action(app):
            loop = asyncio.get_running_loop()
            sid = await app.sessions.create('A')
            result = await app.runs.start(sid, [('user', 'task')])
            return loop, result
        loop, result = bridge.call(action)
        assert loop is bridge._loop and result['messages'][-1].content == 'answer'
    finally:
        bridge.close()


def test_cli_dispatch_rejects_waiting_on_its_owner_thread():
    from threading import current_thread
    from naiyue_test_agent.cli.cli_async import AsyncCliApplication
    bridge = object.__new__(AsyncCliApplication)
    bridge._thread = current_thread()
    bridge._closed = False
    bridge._app = object()
    def must_not_dispatch(*args):
        pytest.fail('owner-thread reentry must be rejected before dispatch')
    bridge._loop = object()
    from unittest.mock import patch
    with patch('asyncio.run_coroutine_threadsafe', must_not_dispatch):
        with pytest.raises(RuntimeError, match='owning event loop'):
            bridge.call(lambda app: None)


def test_domains_share_owner_and_close_drains_nested_cross_domain_operation(monkeypatch):
    async def scenario():
        from naiyue_test_agent.application.async_application import build_async_local_application
        from naiyue_test_agent import application
        from naiyue_test_agent.model_access import ModelCatalog
        model = StreamingToolModel(replies=[])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None)
        entered, release, close_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pending = closing = None
        try:
            owner = app._lifetime
            for name in ('sessions', 'runs', 'history', 'tasks', 'models', 'tools', 'subagents', 'mcp', 'observability'):
                api = getattr(app, name)
                assert api._owner is owner
                assert all(value is not app for value in vars(api).values())
            sid = await app.sessions.create('A')
            original_status = app.sessions._persistence.astatus
            original_stop = app.sessions._sessions.stop_loaded_sessions
            observed = []

            async def held_status(session_id):
                entered.set()
                await release.wait()
                observed.extend(await app.history.messages(session_id))
                return await original_status(session_id)

            def stopped():
                original_stop()
                close_started.set()

            monkeypatch.setattr(app.sessions._persistence, 'astatus', held_status)
            monkeypatch.setattr(app.sessions._sessions, 'stop_loaded_sessions', stopped)
            pending = asyncio.create_task(app.sessions.status(sid))
            await entered.wait()
            closing = asyncio.create_task(app.aclose())
            await close_started.wait()
            with pytest.raises(RuntimeError, match='owning event loop'):
                await app.history.messages(sid)
            assert not closing.done() and len(owner._operations) == 1
            release.set()
            result = await pending
            await closing
            assert result['session_id'] == sid and observed == []
            assert not owner._operations and owner._closed
        finally:
            release.set()
            if pending is not None:
                await pending
            await app.aclose()
    asyncio.run(scenario())
