"""One application type owns typed domain APIs and both storage profiles."""
import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from test_async_application import StreamingToolModel


def test_project_operations_have_no_default_store_adoption_parameters():
    from inspect import signature
    from naiyue_test_agent.application.projects.service import ProjectService
    from naiyue_test_agent.application.projects.operations import resume
    from naiyue_test_agent.storage.project_databases import legacy_guard, read_legacy, deletion_guard
    for entry in (ProjectService, resume, legacy_guard, read_legacy, deletion_guard):
        assert 'owned_store' not in signature(entry).parameters


@pytest.mark.parametrize('disk', [False, True])
def test_one_application_type_exposes_explicit_hosts_and_no_legacy_facade(monkeypatch, tmp_path, disk):
    async def scenario():
        from naiyue_test_agent.application.core import Application
        from naiyue_test_agent.application.service_application import open_service_application
        from naiyue_test_agent.settings import AppSettings
        from naiyue_test_agent import application
        from naiyue_test_agent.model_access import ModelCatalog
        model = StreamingToolModel(replies=[AIMessage(content='old'), AIMessage(content='new')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        settings = AppSettings.model_validate({'storage': {'data_dir': tmp_path / 'data'}})
        async with open_service_application('unused', tools=[], system_prompt='', response_format=None, settings=settings) as app:
            assert type(app) is Application and Application.__bases__ == (object,)
            assert not any(hasattr(app, old) for old in ('sessions', 'runs', 'arun', 'aget_chat_page', 'acreate_session', 'alist_sessions'))
            assert all(getattr(app, name) is not None for name in ('models', 'projects', 'chats', 'temporary'))
            host = await app.chats.open() if disk else await app.temporary.open('ram-profile')
            assert all(getattr(host, name) is not None for name in ('sessions','runs','history','tasks','memory','tools','subagents','mcp','observability'))
            sid = await host.sessions.create('A')
            await host.runs.start(sid, [('user', 'task')])
            saved = await host.sessions.status(sid)
            result = await host.runs.regenerate(sid, request_id=str(uuid4()),
                expected_reply_id=saved['last_reply_id'], expected_conversation_revision=0)
            assert result['messages'][-1].content == 'new'
            assert len((await host.history.page(sid, limit=20, body_bytes=20000, preview_chars=100))['records']) == 2
            assert app.models.default_id == 'A' and app.models.ids == ('A',)
    asyncio.run(scenario())


def test_terminal_has_network_loop_and_no_application_ownership(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from terminal_service_helpers import terminal_runtime
    model = StreamingToolModel(replies=[AIMessage(content='answer')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    with terminal_runtime(tmp_path) as (client, probe):
        assert not hasattr(client, '_app') and not hasattr(client, 'get_model_usage')
        assert not hasattr(client, 'sessions') and not hasattr(client, 'models')
        assert client._loop is not probe.loop
        sid = client.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
        assert client.run(sid, 'task')['messages'][-1]['content'] == 'answer'
    assert not client._thread.is_alive()


def test_terminal_dispatch_rejects_waiting_on_its_owner_thread():
    from threading import current_thread
    from naiyue_test_agent.cli.terminal_client import TerminalClient
    client = object.__new__(TerminalClient)
    client._thread = current_thread()
    client._closed = False
    client._loop = object()
    def must_not_dispatch(*args):
        pytest.fail('owner-thread reentry must be rejected before dispatch')
    from unittest.mock import patch
    with patch('asyncio.run_coroutine_threadsafe', must_not_dispatch):
        with pytest.raises(RuntimeError, match='owning event loop'):
            client.request('GET', '/models')


def test_domains_share_owner_and_close_drains_nested_cross_domain_operation(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.application.service_application import open_service_application
        from naiyue_test_agent.settings import AppSettings
        from naiyue_test_agent import application
        from naiyue_test_agent.model_access import ModelCatalog
        model = StreamingToolModel(replies=[])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        manager = open_service_application('unused', tools=[], system_prompt='', response_format=None,
            settings=AppSettings.model_validate({'storage': {'data_dir': tmp_path / 'data'}}))
        app = await manager.__aenter__()
        host = await app.temporary.open('cross-domain')
        entered, release, close_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pending = closing = None
        try:
            owner = host.sessions._owner
            assert owner._parent is app._lifetime
            assert app.models._owner is app.projects._owner is app._lifetime
            for name in ('sessions', 'runs', 'history', 'tasks', 'memory', 'tools', 'subagents', 'mcp', 'observability'):
                api = getattr(host, name)
                assert api._owner is owner
                assert all(value is not app for value in vars(api).values())
            sid = await host.sessions.create('A')
            original_status = host.sessions._persistence.astatus
            original_stop = host.sessions._sessions.stop_loaded_sessions
            observed = []

            async def held_status(session_id):
                entered.set()
                await release.wait()
                observed.extend(await host.history.messages(session_id))
                return await original_status(session_id)

            def stopped():
                original_stop()
                close_started.set()

            monkeypatch.setattr(host.sessions._persistence, 'astatus', held_status)
            monkeypatch.setattr(host.sessions._sessions, 'stop_loaded_sessions', stopped)
            pending = asyncio.create_task(host.sessions.status(sid))
            await entered.wait()
            closing = asyncio.create_task(app.aclose())
            await close_started.wait()
            with pytest.raises(RuntimeError, match='owning event loop'):
                await host.history.messages(sid)
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
            await manager.__aexit__(None, None, None)
    asyncio.run(scenario())
