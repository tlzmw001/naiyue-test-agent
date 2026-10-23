"""Refresh admission, failures and cleanup preserve existing application owners."""

import asyncio
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.mcp_client.owner import MCPResourceError
from naiyue_test_agent.mcp_client.settings import MCPServerSettings
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import facts, process_alive, stdio_settings, until, http_server
from test_mcp_runtime_configuration import empty_app, write_config


def test_queued_borrow_after_disable_does_not_start_a_new_process(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'queued.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {'text': 'wait'}), AIMessage(content='done')],
                                    server=server, mode='BypassApprovals')
        try:
            first, second = await app.sessions.create('A'), await app.sessions.create('A')
            run = asyncio.create_task(app.runs.start(first, [('user', 'hold slot')]))
            await until(lambda: bool(calls(path)))
            async def queued():
                async with app.sessions._sessions.borrow_mcp(second, 'root', 'local'):
                    raise AssertionError('Disabled queued borrower must not receive a client')
            waiter = asyncio.create_task(queued())
            await until(lambda: waiter in app.sessions._sessions.mcp_resources._requests)
            write_config(config, {'local': server.model_copy(update={'enabled': False})})
            await app.mcp.refresh(config)
            starts = len([r for r in facts(path) if r['event'] == 'start'])
            path.with_suffix('.release').touch()
            await run
            with pytest.raises(MCPResourceError, match='mcp_server_not_enabled'):
                await waiter
            assert len([r for r in facts(path) if r['event'] == 'start']) == starts
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('persistent', [False, True])
def test_reenable_rotates_remembered_authority_and_preserves_selection(monkeypatch, tmp_path, persistent):
    async def scenario():
        path, config = tmp_path / 'memory.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        model = StreamingToolModel(replies=[call(NAME, {}, 'first'), AIMessage(content='one'),
            call(NAME, {}, 'second'), AIMessage(content='two')])
        settings = AppSettings.model_validate({'mcp': {'servers': {'local': server.model_dump()}}})
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        if persistent:
            context = open_async_persistent_application('unused', tools=[], system_prompt='', response_format=None,
                settings=settings, initial_active_tools=[NAME], database_path=tmp_path / 'state.sqlite')
        else:
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def opened():
                from naiyue_test_agent.application.async_application import build_async_local_application
                app = await build_async_local_application('unused', tools=[], system_prompt='', response_format=None,
                    settings=settings, initial_active_tools=[NAME])
                try: yield app
                finally: await app.aclose()
            context = opened()
        async with context as app:
            sid = await app.sessions.create('A')
            await app.runs.start(sid, [('user', 'one')])
            await app.runs.resume_approval(sid, ['approve'], remember_approved=['first'])
            write_config(config, {})
            await app.mcp.refresh(config)
            assert app.sessions.active_tools(sid) == (NAME,)
            write_config(config, {'local': server})
            await app.mcp.refresh(config)
            pending = await app.runs.start(sid, [('user', 'two')])
            assert pending['approval_requests'] and len(calls(path)) == 1
            await app.runs.resume_approval(sid, ['approve'])
            assert len(calls(path)) == 2
    asyncio.run(scenario())


def test_duplicate_name_rejects_whole_server_publication(monkeypatch, tmp_path):
    async def scenario():
        config = tmp_path / 'settings.toml'
        app, _ = await empty_app(monkeypatch, [])
        try:
            await app.tools.register(definition('mcp__local__other'))
            write_config(config, {'local': stdio_settings(tmp_path / 'collision.jsonl', 'stateful', allowed_tools=['echo', 'other'])})
            result = await app.mcp.refresh(config)
            assert result['servers'][0]['status'] == 'error'
            assert [s.tool.name for s in app.tools.list()] == ['mcp__local__other']
            assert 'local' not in app.sessions._sessions.mcp_resources._configs
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', ['missing', 'toml', 'schema', 'negative_ttl'])
def test_bad_configuration_never_replaces_effective_state(monkeypatch, tmp_path, invalid):
    async def scenario():
        config = tmp_path / 'settings.toml'
        path = tmp_path / 'state.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [])
        try:
            original = app.tools.list()[0]
            if invalid == 'toml': config.write_text('PRIVATE-NOT-TOML')
            elif invalid == 'schema': config.write_text('[mcp.servers.bad]\ntransport="stdio"\n')
            elif invalid == 'negative_ttl':
                server = stdio_settings(path, 'stateful', allowed_tools=['echo']).model_dump()
                server['idle_timeout_seconds'] = -3
                write_config(config, {'local': server})
            with pytest.raises(ValueError, match='MCP settings'):
                await app.mcp.refresh(config)
            assert app.tools.list()[0] is original and original.permissions.unavailable_reason() is None
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_candidate_is_cancelled_and_cleaned_when_application_closes(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'closing.jsonl', tmp_path / 'settings.toml'
        app, _ = await empty_app(monkeypatch, [])
        write_config(config, {'local': stdio_settings(path, 'wait', allowed_tools=['echo'])})
        task = asyncio.create_task(app.mcp.refresh(config))
        await until(lambda: any(r['event'] == 'list' for r in facts(path)))
        await asyncio.wait_for(app.aclose(), 15)
        with pytest.raises(MCPResourceError, match='closed'):
            await task
        assert not app.sessions._sessions.mcp_resources._candidates
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
        assert app.tools._tool_registry.names() == ()
    asyncio.run(scenario())


def test_two_concurrent_refreshes_publish_once_and_leave_no_discovery_clients(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'parallel.jsonl', tmp_path / 'settings.toml'
        app, model = await empty_app(monkeypatch, [])
        try:
            write_config(config, {'local': stdio_settings(path, 'stateful', allowed_tools=['echo'])})
            first, second = await asyncio.gather(app.mcp.refresh(config), app.mcp.refresh(config))
            assert first == second and len(app.tools.list()) == 1
            assert len([r for r in facts(path) if r['event'] == 'start']) == 1
            assert not calls(path) and model.seen == []
            assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('json_response', [False, True])
def test_runtime_add_http_and_disable_closes_local_clients(monkeypatch, tmp_path, json_response):
    async def scenario():
        path, config = tmp_path / 'http.jsonl', tmp_path / 'settings.toml'
        async with http_server(path, 'stateful', json_response=json_response) as url:
            server = MCPServerSettings(enabled=True, transport='streamable_http', url=url, allowed_tools=['echo'])
            app, _ = await empty_app(monkeypatch, [call(NAME, {}), AIMessage(content='done')],
                settings=AppSettings.model_validate({'permissions': {'mode': 'BypassApprovals'}}))
            try:
                write_config(config, {'local': server})
                assert (await app.mcp.refresh(config))['servers'][0]['status'] == 'enabled'
                sid = await app.sessions.create('A')
                await app.sessions.select_tools(sid, [NAME])
                await app.runs.start(sid, [('user', 'HTTP')])
                write_config(config, {})
                await app.mcp.refresh(config)
                assert not app.sessions._sessions.mcp_resources._owners and len(calls(path)) == 1
            finally:
                await app.aclose()
    asyncio.run(scenario())
