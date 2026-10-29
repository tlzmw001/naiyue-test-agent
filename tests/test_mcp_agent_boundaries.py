"""Failure, output and assembly boundaries for the root MCP adapter."""

from runtime_context_helpers import ExecutionRoot
from dataclasses import replace
from runtime_context_helpers import RunIdentity, ToolAuthorization, typed_context
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.mcp_client.settings import MCPServerSettings
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import PermissionMechanismError, execution_environment
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import facts, http_server, process_alive, stdio_settings


@pytest.mark.parametrize('action', ['normal', 'error', 'media', 'interaction', 'large', 'inside_archive'])
def test_results_use_bounded_feedback_artifact_and_owned_archive(monkeypatch, tmp_path, action, caplog):
    if action == 'inside_archive':
        from pathlib import Path
        monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    async def scenario():
        path = tmp_path / 'output.jsonl'
        server = stdio_settings(path, 'tools', allowed_tools=['echo'], max_result_bytes=512)
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {
            'text': 'large' if action == 'inside_archive' else action}), AIMessage(content='done')],
                                    mode='BypassApprovals', server=server)
        try:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            result = await app.host.runs.start(sid, [('user', action)])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert output.status == ('success' if action in ('normal', 'large', 'inside_archive') else 'error')
            assert len(output.content.encode('utf-8')) <= 512 and len(calls(path)) == 1
            if action == 'large':
                assert output.artifact['truncated'] and output.artifact['archive_complete']
                raw = tmp_path / output.artifact['archive_paths']['raw']
                assert output.artifact['archive_paths']['raw'] in output.content
                assert json.loads(raw.read_text())['content'] == ['大' * 5000]
                assert raw.stat().st_mode & 0o077 == 0
            elif action == 'error':
                assert 'Fixture execution error' in output.content
                assert output.artifact['is_error']
            elif action == 'inside_archive':
                assert output.artifact['outcome'] == 'success' and output.artifact['truncated']
                assert not output.artifact['archive_complete'] and not output.artifact['archive_paths']
                assert output.artifact['archive_reason'] == 'archive_root_unavailable'
                assert not list(tmp_path.rglob('*-raw.log'))
            elif action in ('media', 'interaction'):
                assert 'unsupported' in output.content
                assert 'PRIVATE' not in output.content
            assert 'PRIVATE' not in caplog.text
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('when', ['before-first-connection', 'before-resume'])
def test_catalog_drift_rejects_without_submitting_call(monkeypatch, tmp_path, when):
    async def scenario():
        path = tmp_path / 'drift.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {}), AIMessage(content='done')])
        try:
            sid = await app.host.sessions.create('A')
            if when == 'before-first-connection':
                path.with_suffix('.drift').touch()
            result = await app.host.runs.start(sid, [('user', 'request')])
            assert result['approval_requests']
            path.with_suffix('.drift').touch()
            result = await app.host.runs.resume_approval(sid, ['approve'])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert output.status == 'error' and 'catalog changed' in output.content
            assert calls(path) == []
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_call_deadline_invalidates_connection_without_replay(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'timeout.jsonl'
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'], call_timeout_seconds=1.)
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {'text': 'wait'}), AIMessage(content='done')],
                                    mode='BypassApprovals', server=server)
        try:
            sid = await app.host.sessions.create('A')
            result = await app.host.runs.start(sid, [('user', 'wait')])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert output.status == 'error' and 'timed out' in output.content
            assert len(calls(path)) == 1
            assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('json_response', [False, True])
def test_http_agent_roundtrip_calls_once(monkeypatch, tmp_path, json_response):
    async def scenario():
        path = tmp_path / 'http.jsonl'
        async with http_server(path, 'stateful', json_response) as url:
            server = MCPServerSettings(enabled=True, transport='streamable_http', url=url, allowed_tools=['echo'])
            app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {}), AIMessage(content='done')],
                                        mode='BypassApprovals', server=server)
            try:
                sid = await app.host.sessions.create('A')
                result = await app.host.runs.start(sid, [('user', 'HTTP')])
                output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
                assert output.status == 'success' and output.artifact['structured_content']['count'] == 1
                assert len(calls(path)) == 1
            finally:
                await app.aclose()
    asyncio.run(scenario())


def test_mcp_is_registered_but_default_activity_stays_unchanged(monkeypatch, tmp_path):
    async def scenario():
        app, _ = await make_mcp_app(monkeypatch, tmp_path / 'select.jsonl', [], initial=None)
        try:
            sid = await app.host.sessions.create('A')
            assert app.host.sessions._sessions.mcp_resources._owners == {}
            assert app.host.sessions._sessions.resolve_active_tool_names(sid) == ()
            await app.host.sessions.select_tools(sid, [NAME])
            assert app.host.sessions._sessions.resolve_active_tool_names(sid) == (NAME,)
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_mcp_command_argument_is_not_presented_as_a_local_shell_command(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'command.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {'command': 'business value'})])
        try:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            result = await app.host.runs.start(sid, [('user', 'external tool')])
            row, = result['approval_requests']
            assert 'workspace' not in row and row['remember_scope'] == 'tool'
            assert 'MCP local/echo' in row['target']
            assert not calls(path)
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_persistent_factory_registers_before_selection_and_rules(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'persistent.jsonl'
        model = StreamingToolModel(replies=[call(NAME, {}), AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        settings = AppSettings.model_validate({'mcp': {'servers': {'local': stdio_settings(
            path, 'stateful', allowed_tools=['echo']).model_dump()}}, 'permissions': {'allow': [{'tool': NAME}]}})
        async with open_component_application('unused', tools=[], system_prompt='', response_format=None,
            initial_active_tools=[NAME], settings=settings, database_path=tmp_path / 'db.sqlite') as app:
            sid = await app.host.sessions.create('A')
            result = await app.host.runs.start(sid, [('user', 'MCP')])
            assert not result.get('approval_requests') and len(calls(path)) == 1
            assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).status == 'success'
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['args', 'name', 'identity', 'environment', 'inactive_child'])
def test_executor_checks_permit_before_any_resource_borrow(monkeypatch, tmp_path, change):
    async def scenario():
        path = tmp_path / 'permit.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [])
        try:
            spec, = app.host.tools.list()
            context = typed_context(identity=RunIdentity(session_id=await app.host.sessions.create('A'), agent_path='root'))
            subject = spec.permissions.prepare({}, context)
            subject.update(args={}, execution_environment=execution_environment())
            permit = {'call_id': 'one', 'tool_name': NAME, 'args': {}, 'subject': subject, 'session_id': context.scope.identity.session_id, 'agent_path': context.scope.identity.agent_path}
            if change == 'args': permit['args'] = {'text': 'changed'}
            if change == 'name': permit['tool_name'] = 'mcp__other__echo'
            if change == 'identity': subject['mcp']['config_identity'] = 'changed'
            if change == 'environment': subject['execution_environment'] = {'kind': 'other'}
            if change == 'inactive_child':
                context = replace(context, scope=replace(context.scope, identity=replace(context.scope.identity, agent_path='root/tester/missing')))
                permit['agent_path'] = 'root/tester/missing'
            runtime = SimpleNamespace(tool_call_id='one', context=replace(context, authorization=ToolAuthorization(permit=permit)))
            from langchain_core.tools import ToolException
            if change == 'inactive_child':
                output = await spec.tool.coroutine(runtime=runtime)
                assert isinstance(output, ToolMessage) and output.status == 'error'
                assert output.tool_call_id == 'one' and output.name == NAME
                assert output.artifact['outcome'] == 'not_executed' and 'No tools/call was sent' in output.content
            else:
                with pytest.raises(ToolException if change == 'environment' else PermissionMechanismError):
                    await spec.tool.coroutine(runtime=runtime)
            assert len([r for r in facts(path) if r['event'] == 'start']) == 1 and not calls(path)
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('review_decision', ['allow', 'ask', 'malformed'])
def test_auto_reviews_mcp_subject_and_falls_back_to_human(monkeypatch, tmp_path, review_decision):
    from test_auto_permission_review import model, verdict
    from scripts.component_fixtures import build_component_application
    async def scenario():
        path = tmp_path / 'auto.jsonl'
        primary = StreamingToolModel(replies=[call(NAME, {}), AIMessage(content='done')])
        fast = model([AIMessage(content='PRIVATE-BAD-REVIEW') if review_decision == 'malformed' else verdict(review_decision)])
        detail = model([verdict('ask')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': primary, 'fast': fast, 'detail': detail}))
        settings = AppSettings.model_validate({'mcp': {'servers': {'local': stdio_settings(path, 'stateful',
            allowed_tools=['echo']).model_dump()}}, 'permissions': {'mode': 'Auto', 'auto': {
            'fast_model_id': 'fast', 'detailed_model_id': 'detail'}}})
        app = await build_component_application('unused', tools=[], system_prompt='', response_format=None,
            initial_active_tools=[NAME], settings=settings)
        try:
            sid = await app.host.sessions.create('A')
            result = await app.host.runs.start(sid, [('user', 'call configured MCP tool')])
            data = json.loads(fast.seen[0][-1].content)
            assert data['subject']['mcp']['server_id'] == 'local'
            assert data['subject']['side_effects'] == 'unknown'
            if review_decision == 'allow':
                assert not result.get('approval_requests') and len(calls(path)) == 1
            else:
                assert result['approval_requests'] and not calls(path)
                await app.host.runs.resume_approval(sid, ['reject'])
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_explicit_deny_overrides_bypass_allow_and_remembered_mcp_tool(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import prepare_permission_call
    from naiyue_test_agent.tools.permission_rules import PermissionSettings
    from naiyue_test_agent.tools.permissions import remembered_rule
    async def scenario():
        path = tmp_path / 'deny.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {}), AIMessage(content='done')], mode='BypassApprovals',
            rules={'deny': [{'tool': NAME}], 'allow': [{'tool': NAME}]})
        try:
            sid = await app.host.sessions.create('A')
            spec, = app.host.tools.list()
            context = typed_context(identity=RunIdentity(session_id=sid, agent_path='root'))
            prepared = await prepare_permission_call({'id': 'first', 'name': NAME, 'args': {}}, spec, context, PermissionSettings(), [])
            remembered = remembered_rule(prepared['subject'])
            session = app.host.sessions._sessions.require(sid)
            session._runtime.state.session_allow.append(remembered)
            result = await app.host.runs.start(sid, [('user', 'denied')])
            assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).status == 'error'
            assert not result.get('approval_requests') and not calls(path)
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_remembered_tool_still_relists_live_catalog_before_each_execution(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'live-drift.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {}, 'a'), AIMessage(content='first'),
            call(NAME, {'text': 'second'}, 'b'), AIMessage(content='blocked')])
        try:
            sid = await app.host.sessions.create('A')
            await app.host.runs.start(sid, [('user', 'first')])
            await app.host.runs.resume_approval(sid, ['approve'], remember_approved=['a'])
            assert len(calls(path)) == 1
            path.with_suffix('.drift').touch()
            result = await app.host.runs.start(sid, [('user', 'second')])
            assert not result.get('approval_requests')
            assert [m for m in result['messages'] if isinstance(m, ToolMessage)][-1].status == 'error'
            assert len(calls(path)) == 1
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('root_kind', ['none', 'quota', 'symlink', 'inside_execution'])
def test_result_archive_degradation_is_explicit_and_budgeted(tmp_path, root_kind):
    from naiyue_test_agent.mcp_client.results import format_result
    from mcp.types import CallToolResult, TextContent
    server = stdio_settings(tmp_path / 'unused', max_result_bytes=512, max_response_bytes=64)
    if root_kind == 'symlink':
        outside = tmp_path / 'outside'; outside.mkdir()
        (tmp_path / 'naiyue-agent-output').symlink_to(outside, target_is_directory=True)
    runtime = SimpleNamespace(tool_call_id='one', context=typed_context(archive_root=None if root_kind == 'none' else tmp_path))
    if root_kind == 'inside_execution':
        runtime.context = replace(runtime.context, scope=replace(runtime.context.scope, identity=replace(runtime.context.scope.identity, execution_root=ExecutionRoot(tmp_path) if tmp_path is not None else None)))
    tool = SimpleNamespace(remote_name='echo', registered_name=NAME)
    result = format_result(CallToolResult(content=[TextContent(type='text', text='大' * 5000)]), runtime=runtime,
        server=server, server_id='local', tool=tool)
    assert result.artifact['truncated'] and not result.artifact['archive_complete']
    reason = {'none': 'archive_root_unavailable', 'quota': 'command_limit', 'symlink': 'directory_unavailable',
              'inside_execution': 'archive_root_unavailable'}[root_kind]
    assert result.artifact['archive_reason'] == reason and reason in result.content
    assert len(result.content.encode('utf-8')) <= 512
    assert result.status == 'success' and result.artifact['outcome'] == 'success'
    assert result.tool_call_id == 'one' and result.artifact['source']['remote_tool'] == 'echo'
    if root_kind == 'symlink': assert list(outside.iterdir()) == []
