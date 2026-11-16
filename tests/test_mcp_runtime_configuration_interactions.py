"""Refresh cannot substitute pending authority or leak a candidate owner."""

import asyncio

import pytest
from pydantic import SecretStr
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application, config_paths
from terminal_service_helpers import terminal_runtime
from naiyue_test_agent.cli.cli_mcp import handle_mcp_command
from naiyue_test_agent.mcp_client.owner import ConnectionOwner, MCPResourceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import facts, http_server, process_alive, stdio_settings, until
from test_mcp_runtime_configuration import empty_app, write_config
from test_mcp_subagents import MCPChildModel, make_child_app


def test_pending_child_disable_returns_feedback_and_parent_finishes(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'child.jsonl', tmp_path / 'settings.toml'
        child = MCPChildModel(replies=[call(NAME, {}), AIMessage(content='child handled')])
        app, _ = await make_child_app(monkeypatch, path, child)
        try:
            sid = await app.host.sessions.create('A')
            task = asyncio.create_task(app.host.runs.start(sid, [('user', 'child')]))
            await until(lambda: bool(app.host.runs.pending_subagents(sid)))
            pending = app.host.runs.pending_subagents(sid)[0]
            write_config(config, {})
            await app.host.mcp.refresh(config)
            await app.host.runs.submit_subagent_approval(sid, approval_batch_id=pending['approval_batch_id'],
                origin_run_id=pending['origin_run_id'], decisions=[{'approval_id': pending['items'][0]['approval_id'],
                'decision': 'approve'}])
            result = await task
            assert result['messages'][-1].content == 'root done' and not calls(path)
            child_result = next(m for m in child.seen[-1] if isinstance(m, ToolMessage))
            assert child_result.status == 'error' and child_result.artifact['outcome'] == 'not_executed'
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_disable_during_catalog_preflight_never_sends_business_rpc(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'preflight.jsonl', tmp_path / 'settings.toml'
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {}), AIMessage(content='handled')], mode='BypassApprovals')
        entered, release = asyncio.Event(), asyncio.Event()
        from naiyue_test_agent.mcp_client import tools
        original = tools.read_catalog
        async def held(*args):
            result = await original(*args)
            entered.set(); await release.wait()
            return result
        monkeypatch.setattr(tools, 'read_catalog', held)
        try:
            sid = await app.host.sessions.create('A')
            task = asyncio.create_task(app.host.runs.start(sid, [('user', 'preflight')]))
            await entered.wait()
            write_config(config, {})
            await app.host.mcp.refresh(config)
            release.set()
            result = await task
            feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert feedback.artifact['outcome'] == 'not_executed' and not calls(path)
            assert result['messages'][-1].content == 'handled'
        finally:
            release.set(); await app.aclose()
    asyncio.run(scenario())


def test_external_refresh_cancellation_closes_candidate_and_publishes_nothing(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'cancel.jsonl', tmp_path / 'settings.toml'
        app, _ = await empty_app(monkeypatch, [])
        try:
            write_config(config, {'local': stdio_settings(path, 'wait', allowed_tools=['echo'])})
            task = asyncio.create_task(app.host.mcp.refresh(config))
            await until(lambda: any(r['event'] == 'list' for r in facts(path)))
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not app.host.sessions._sessions.mcp_resources._candidates and not app.host.tools.list()
            assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_candidate_cleanup_failure_prevents_publication_and_remains_required(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'cleanup.jsonl', tmp_path / 'settings.toml'
        app, _ = await empty_app(monkeypatch, [])
        close = ConnectionOwner.close
        async def fail_after_close(owner):
            await close(owner)
            raise MCPResourceError('synthetic_cleanup_failure')
        monkeypatch.setattr(ConnectionOwner, 'close', fail_after_close)
        write_config(config, {'local': stdio_settings(path, 'stateful', allowed_tools=['echo'])})
        result = await app.host.mcp.refresh(config)
        assert result['servers'][0]['status'] == 'error' and not app.host.tools.list()
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
        with pytest.raises(ExceptionGroup): await app.aclose()
        assert app.root._lifetime._closed
    asyncio.run(scenario())


def test_connection_changes_are_explicit_and_non_mcp_settings_are_not_applied(monkeypatch, tmp_path, caplog):
    async def scenario():
        path, config = tmp_path / 'old.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, _ = await make_mcp_app(monkeypatch, path, [], server=server)
        try:
            original = app.host.tools.list()[0]
            changed = server.model_copy(update={'args': [*server.args, '--PRIVATE']})
            write_config(config, {'local': changed})
            result = await app.host.mcp.refresh(config)
            assert result['servers'][0]['error'] == 'connection_configuration_changed'
            assert result['servers'][0]['changed_fields'] == ['args']
            assert result['servers'][0]['enabled'] and app.host.tools.list()[0] is original
            assert 'code=connection_configuration_changed changed_fields=args' in caplog.text
            assert '--PRIVATE' not in caplog.text + str(result)
            write_config(config, {'local': server})
            with config.open('a') as stream: stream.write('\n[permissions]\nmode="NoAsk"\n[network]\nmode="direct"\n')
            restored = await app.host.mcp.refresh(config)
            assert 'changed_fields' not in restored['servers'][0]
            sid = await app.host.sessions.create('A')
            assert app.host.sessions.permission_mode(sid) == 'Default'
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['budget', 'headers', 'credential_environment'])
def test_refresh_reports_changed_field_names_without_values(monkeypatch, tmp_path, caplog, change):
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    async def scenario():
        path, config = tmp_path / 'diagnostic.jsonl', tmp_path / 'settings.toml'
        monkeypatch.setenv('MCP_REFRESH_TEST_SECRET', 'PRIVATE_OLD_TOKEN')
        async with http_server(path, 'stateful', True) as url:
            server = MCPServerSettings(enabled=True, transport='streamable_http', url=url, allowed_tools=['echo'],
                headers={'Authorization': 'Bearer PRIVATE_OLD_HEADER'} if change == 'headers' else {},
                headers_env={'Authorization': 'MCP_REFRESH_TEST_SECRET'} if change == 'credential_environment' else {})
            app, _ = await make_mcp_app(monkeypatch, path, [], server=server)
            try:
                original = app.host.tools.list()[0]
                if change == 'budget':
                    candidate = server.model_copy(update={'call_timeout_seconds': 123.0})
                    expected = 'call_timeout_seconds'
                elif change == 'headers':
                    candidate = server.model_copy(update={'headers': {'Authorization': SecretStr('Bearer PRIVATE_NEW_HEADER')}})
                    expected = 'headers'
                else:
                    monkeypatch.setenv('MCP_REFRESH_TEST_SECRET', 'PRIVATE_NEW_TOKEN')
                    candidate, expected = server, 'credential_environment'
                values = candidate.model_dump(exclude={'headers'}, exclude_none=True)
                values['headers'] = {key: value.get_secret_value() for key, value in candidate.headers.items()}
                write_config(config, {'local': values})
                result = await app.host.mcp.refresh(config)
                assert result['servers'][0]['error'] == 'connection_configuration_changed'
                assert result['servers'][0]['changed_fields'] == [expected]
                assert app.host.tools.list()[0] is original and calls(path) == []
                assert f'code=connection_configuration_changed changed_fields={expected}' in caplog.text
                serialized = str(result) + caplog.text
                assert 'PRIVATE_' not in serialized and 'MCP_REFRESH_TEST_SECRET' not in serialized and url not in serialized
            finally:
                await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('transport', ['stdio', 'streamable_http'])
def test_unchanged_config_with_omitted_defaults_refreshes_remote_schema(monkeypatch, tmp_path, transport):
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    async def scenario():
        path, config = tmp_path / 'unchanged.jsonl', tmp_path / 'settings.toml'
        async def check(server):
            write_config(config, {'local': server.model_dump(exclude_defaults=True, exclude_none=True)})
            original_bytes = config.read_bytes()
            assert 'timeout_seconds' not in original_bytes.decode()
            app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {'text': 'before'}), AIMessage(content='one'),
                call(NAME, {'trace_id': 'after'}), AIMessage(content='two')], server=server, mode='BypassApprovals')
            try:
                sid = await app.host.sessions.create('A')
                await app.host.runs.start(sid, [('user', 'before server update')])
                path.with_suffix('.schema-v2').touch()
                result = await app.host.mcp.refresh(config)
                assert result['servers'][0]['status'] == 'enabled'
                assert 'changed_fields' not in result['servers'][0]
                assert app.host.tools.list()[0].input_schema['required'] == ['trace_id']
                await app.host.runs.start(sid, [('user', 'after server update')])
                assert [row['arguments'] for row in calls(path)] == [{'text': 'before'}, {'trace_id': 'after'}]
                assert config.read_bytes() == original_bytes
            finally:
                await app.aclose()
        if transport == 'stdio':
            await check(stdio_settings(path, 'stateful', allowed_tools=['echo']))
        else:
            async with http_server(path, 'stateful', True) as url:
                await check(MCPServerSettings(enabled=True, transport='streamable_http', url=url, allowed_tools=['echo']))
    asyncio.run(scenario())


def test_terminal_bridge_adds_and_uses_tool_in_its_existing_loop(monkeypatch, tmp_path):
    path, config = tmp_path / 'terminal.jsonl', tmp_path / 'settings.toml'
    monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: tmp_path)
    model = StreamingToolModel(replies=[call(NAME, {}), AIMessage(content='CLI done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    lines = []
    with terminal_runtime(tmp_path, settings=AppSettings.model_validate({
            'permissions': {'mode': 'BypassApprovals'}}), settings_path=config) as (client, probe):
        sid = client.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
        write_config(config, {'local': stdio_settings(path, 'stateful', allowed_tools=['echo'])})
        handle_mcp_command(client, sid, ':mcp-refresh', lines.append)
        handle_mcp_command(client, sid, ':tools +mcp:local', lines.append)
        result = client.run(sid, 'new tool')
        assert result['messages'][-1]['content'] == 'CLI done' and len(calls(path)) == 1
    assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
