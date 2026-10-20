"""Refresh cannot substitute pending authority or leak a candidate owner."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application, config_paths
from naiyue_test_agent.application.cli_async import AsyncCliApplication
from naiyue_test_agent.application.cli_mcp import handle_mcp_command
from naiyue_test_agent.mcp_client.owner import ConnectionOwner, MCPResourceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import facts, process_alive, stdio_settings, until
from test_mcp_runtime_configuration import empty_app, write_config
from test_mcp_subagents import MCPChildModel, make_child_app


def test_pending_child_disable_returns_feedback_and_parent_finishes(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'child.jsonl', tmp_path / 'settings.toml'
        child = MCPChildModel(replies=[call(NAME, {}), AIMessage(content='child handled')])
        app, _ = await make_child_app(monkeypatch, path, child)
        try:
            sid = await app.acreate_session('A')
            task = asyncio.create_task(app.arun(sid, [('user', 'child')]))
            await until(lambda: bool(app.pending_subagent_approvals(sid)))
            pending = app.pending_subagent_approvals(sid)[0]
            write_config(config, {})
            await app.arefresh_mcp(config)
            await app.asubmit_subagent_approval(sid, approval_batch_id=pending['approval_batch_id'],
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
            sid = await app.acreate_session('A')
            task = asyncio.create_task(app.arun(sid, [('user', 'preflight')]))
            await entered.wait()
            write_config(config, {})
            await app.arefresh_mcp(config)
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
            task = asyncio.create_task(app.arefresh_mcp(config))
            await until(lambda: any(r['event'] == 'list' for r in facts(path)))
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not app._sessions.mcp_resources._candidates and not app.get_registered_tools()
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
        result = await app.arefresh_mcp(config)
        assert result['servers'][0]['status'] == 'error' and not app.get_registered_tools()
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
        with pytest.raises(ExceptionGroup): await app.aclose()
        assert app._closed
    asyncio.run(scenario())


def test_connection_changes_are_explicit_and_non_mcp_settings_are_not_applied(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'old.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, _ = await make_mcp_app(monkeypatch, path, [], server=server)
        try:
            original = app.get_registered_tools()[0]
            changed = server.model_copy(update={'args': [*server.args, '--PRIVATE']})
            write_config(config, {'local': changed})
            result = await app.arefresh_mcp(config)
            assert result['servers'][0]['error'] == 'connection_configuration_changed'
            assert result['servers'][0]['enabled'] and app.get_registered_tools()[0] is original
            write_config(config, {'local': server})
            with config.open('a') as stream: stream.write('\n[permissions]\nmode="NoAsk"\n[network]\nmode="direct"\n')
            await app.arefresh_mcp(config)
            sid = await app.acreate_session('A')
            assert app.get_permission_mode(sid) == 'Default'
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_terminal_bridge_adds_and_uses_tool_in_its_existing_loop(monkeypatch, tmp_path):
    path, config = tmp_path / 'terminal.jsonl', tmp_path / 'settings.toml'
    monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: tmp_path)
    model = StreamingToolModel(replies=[call(NAME, {}), AIMessage(content='CLI done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = AsyncCliApplication('unused', tools=[], system_prompt='', response_format=None,
        settings=AppSettings.model_validate({'permissions': {'mode': 'BypassApprovals'}}))
    lines = []
    try:
        sid = app.create_session('A')
        write_config(config, {'local': stdio_settings(path, 'stateful', allowed_tools=['echo'])})
        handle_mcp_command(app, sid, ':mcp-refresh', lines.append)
        handle_mcp_command(app, sid, ':tools +' + NAME, lines.append)
        result = app.run(sid, [('user', 'new tool')])
        assert result['messages'][-1].content == 'CLI done' and len(calls(path)) == 1
    finally:
        app.close()
    assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
