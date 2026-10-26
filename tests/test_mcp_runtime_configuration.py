"""Runtime MCP changes use the real graph and scoped SDK-owned connections."""

import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from scripts.component_fixtures import build_component_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import facts, process_alive, stdio_settings, until


def write_config(path, servers):
    lines = []
    for name, server in servers.items():
        values = server.model_dump(exclude_none=True) if hasattr(server, 'model_dump') else server
        lines.append(f'[mcp.servers.{name}]')
        for key, value in values.items():
            if not isinstance(value, dict):
                lines.append(f'{key} = {json.dumps(value)}')
        for key, values in values.items():
            if isinstance(values, dict) and values:
                lines.append(f'[mcp.servers.{name}.{key}]')
                lines.extend(f'{key} = {json.dumps(value)}' for key, value in values.items())
    path.write_text('\n'.join(lines) if lines else '[mcp]\nservers = {}\n')


async def empty_app(monkeypatch, replies, *, settings=None):
    model = StreamingToolModel(replies=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_component_application('unused', tools=[], system_prompt='', response_format=None,
        settings=settings or AppSettings())
    return app, model


def test_add_after_zero_server_start_select_and_call_without_rebuilding(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'zero.jsonl', tmp_path / 'settings.toml'
        app, model = await empty_app(monkeypatch, [call(NAME, {'text': 'one'}), AIMessage(content='done')])
        graph = app.host.runs._agent
        try:
            sid, other = await app.host.sessions.create('A'), await app.host.sessions.create('A')
            server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
            write_config(config, {'local': server})
            report = await app.host.mcp.refresh(config)
            assert report['servers'][0]['status'] == 'enabled'
            assert [s.tool.name for s in app.host.tools.list()] == [NAME]
            assert app.host.sessions.active_tools(sid) == app.host.sessions.active_tools(other) == ()
            assert app.host.sessions.active_tools(await app.host.sessions.create('A')) == ()
            assert not model.seen and not calls(path)
            await app.host.sessions.select_tools(sid, [NAME])
            await app.host.runs.start(sid, [('user', 'call')])
            result = await app.host.runs.resume_approval(sid, ['approve'])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert output.status == 'success' and len(calls(path)) == 1
            assert app.host.runs._agent is graph
        finally:
            await app.aclose()
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())


def test_append_allowlist_keeps_process_and_unchanged_refresh_keeps_authority(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'same.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {}), AIMessage(content='one'),
            call('mcp__local__other', {}), AIMessage(content='two')], server=server, mode='BypassApprovals')
        try:
            sid = await app.host.sessions.create('A')
            await app.host.runs.start(sid, [('user', 'one')])
            first = app.host.tools.list()[0].permissions.identity()
            write_config(config, {'local': server})
            await app.host.mcp.refresh(config)
            assert app.host.tools.list()[0].permissions.identity() == first
            expanded = server.model_copy(update={'allowed_tools': ['echo', 'other']})
            write_config(config, {'local': expanded})
            await app.host.mcp.refresh(config)
            await app.host.sessions.select_tools(sid, ['mcp__local__other'])
            await app.host.runs.start(sid, [('user', 'two')])
            assert [r['count'] for r in calls(path)] == [1, 2]
            assert len({r['pid'] for r in calls(path)}) == 1
            assert app.host.tools.list()[0].permissions.identity() != first
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['disable', 'remove', 'catalog'])
def test_changed_pending_call_is_not_executed_and_agent_continues(monkeypatch, tmp_path, change):
    async def scenario():
        path, config = tmp_path / 'pending.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, model = await make_mcp_app(monkeypatch, path, [call(NAME, {}), AIMessage(content='handled')], server=server)
        try:
            sid = await app.host.sessions.create('A')
            await app.host.runs.start(sid, [('user', 'old request')])
            if change == 'catalog':
                path.with_suffix('.drift').touch()
            changed = server.model_copy(update={'enabled': False} if change == 'disable' else
                {'allowed_tools': [] if change == 'remove' else ['echo', 'other']})
            write_config(config, {'local': changed})
            await app.host.mcp.refresh(config)
            result = await app.host.runs.resume_approval(sid, ['approve'])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert output.status == 'error' and output.tool_call_id == 't'
            assert output.artifact['outcome'] == 'not_executed' and not calls(path)
            assert result['messages'][-1].content == 'handled' and isinstance(model.seen[-1][-1], ToolMessage)
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_disable_drains_submitted_call_and_reenable_does_not_replay(monkeypatch, tmp_path):
    async def scenario():
        path, config = tmp_path / 'drain.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {'text': 'wait'}), AIMessage(content='one'),
            call(NAME, {}), AIMessage(content='two')], server=server, mode='BypassApprovals')
        try:
            sid = await app.host.sessions.create('A')
            task = asyncio.create_task(app.host.runs.start(sid, [('user', 'one')]))
            await until(lambda: bool(calls(path)))
            pid = calls(path)[0]['pid']
            write_config(config, {'local': server.model_copy(update={'enabled': False})})
            report = await app.host.mcp.refresh(config)
            assert report['servers'][0]['status'] == 'disabled' and report['servers'][0]['draining']
            assert process_alive(pid) and not task.done()
            path.with_suffix('.release').touch()
            result = await task
            assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).status == 'success'
            assert not process_alive(pid)
            write_config(config, {'local': server})
            await app.host.mcp.refresh(config)
            await app.host.runs.start(sid, [('user', 'two')])
            assert len(calls(path)) == 2 and calls(path)[1]['pid'] != pid
            assert calls(path)[1]['count'] == 1
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_bad_new_server_preserves_live_server_identity_and_connection(monkeypatch, tmp_path, caplog):
    async def scenario():
        path, config = tmp_path / 'ok.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {}), AIMessage(content='one'),
            call(NAME, {}), AIMessage(content='two')], server=server, mode='BypassApprovals')
        try:
            sid = await app.host.sessions.create('A')
            await app.host.runs.start(sid, [('user', 'one')])
            original = app.host.tools.list()[0].permissions.identity()
            bad = stdio_settings(tmp_path / 'bad.jsonl', 'invalid', allowed_tools=['echo'])
            write_config(config, {'local': server, 'bad': bad})
            report = await app.host.mcp.refresh(config)
            assert next(r for r in report['servers'] if r['server_id'] == 'bad')['status'] == 'error'
            assert original == app.host.tools.list()[0].permissions.identity()
            await app.host.runs.start(sid, [('user', 'two')])
            assert [r['count'] for r in calls(path)] == [1, 2]
            assert len({r['pid'] for r in calls(path)}) == 1
            assert 'PRIVATE' not in json.dumps(report) + caplog.text
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('keep_builtin', [False, True])
def test_disabled_tools_are_excluded_from_next_context_estimate(monkeypatch, tmp_path, keep_builtin):
    async def scenario():
        path, config = tmp_path / 'context.jsonl', tmp_path / 'settings.toml'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        app, _ = await make_mcp_app(monkeypatch, path, [], server=server)
        try:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            if keep_builtin:
                from test_dynamic_tools import definition
                await app.host.tools.register(definition())
                await app.host.sessions.select_tools(sid, [NAME, 'probe'])
            write_config(config, {'local': server.model_copy(update={'enabled': False})})
            await app.host.mcp.refresh(config)
            view = await app.host.history.context(sid)
            assert view['current']['basis'] == 'next_request'
            assert view['current']['estimate']['status'] == 'available'
            groups = view['current']['estimate']['groups']
            if keep_builtin:
                assert next(g for g in groups if g['id'] == 'tool_definitions')['count'] == 1
            else:
                # The existing estimator omits zero-count/zero-token groups.
                assert all(g['id'] != 'tool_definitions' for g in groups)
        finally:
            await app.aclose()
    asyncio.run(scenario())
