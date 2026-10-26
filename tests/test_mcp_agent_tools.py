"""Root Agent MCP calls go through the formal graph and real SDK transport."""

import asyncio
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from scripts.component_fixtures import build_component_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.permissions import matches_session_allow, remembered_rule
from test_async_application import StreamingToolModel, call
from test_mcp_connections import facts, process_alive, stdio_settings, until


NAME = 'mcp__local__echo'


def subject(**changes):
    result = {'tool_name': NAME, 'operation': 'mcp_call', 'workspace': None,
              'execution_environment': {'kind': 'host', 'platform': 'linux'},
              'session_id': 'one', 'args': {'command': 'first'},
              'mcp': {'server_id': 'local', 'remote_tool': 'echo',
                      'config_identity': 'config', 'catalog_fingerprint': 'catalog'}}
    result.update(changes)
    return result


def test_remembered_mcp_tool_accepts_other_arguments_but_not_other_identity():
    original = subject()
    rule = remembered_rule(original)
    assert 'args' not in rule and 'command' not in rule
    assert matches_session_allow([rule], subject(args={'command': 'second'}))
    for key, value in [('tool_name', 'mcp__other__echo'), ('session_id', 'two')]:
        assert not matches_session_allow([rule], subject(**{key: value}))
    for key in original['mcp']:
        changed = deepcopy(original)
        changed['mcp'][key] += '-changed'
        assert not matches_session_allow([rule], changed)
    assert not matches_session_allow([rule], {k: v for k, v in original.items() if k != 'mcp'})


async def make_mcp_app(monkeypatch, path, replies, *, mode='Default', rules=None, server=None, initial=(NAME,)):
    model = StreamingToolModel(replies=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    settings = AppSettings.model_validate({'mcp': {'servers': {'local': (server or stdio_settings(
        path, 'stateful', allowed_tools=['echo'])).model_dump()}}, 'permissions': {'mode': mode, **(rules or {})}})
    app = await build_component_application('unused', tools=[], system_prompt='', response_format=None,
        initial_active_tools=initial, settings=settings)
    return app, model


def calls(path):
    return [row for row in facts(path) if row['event'] == 'call']


@pytest.mark.parametrize('action', ['approve', 'reject', 'NoAsk', 'BypassApprovals', 'allow', 'deny', 'invalid'])
def test_root_agent_permission_call_result_and_events(monkeypatch, tmp_path, action):
    async def scenario():
        path = tmp_path / 'graph.jsonl'
        rules = {action: [{'tool': NAME}]} if action in ('allow', 'deny') else {}
        mode = action if action in ('NoAsk', 'BypassApprovals') else 'Default'
        args = {'text': 3} if action == 'invalid' else {'text': 'hello'}
        app, model = await make_mcp_app(monkeypatch, path, [call(NAME, args), AIMessage(content='done')], mode=mode, rules=rules)
        events = []; app.host.observability.subscribe(events.append)
        try:
            spec, = app.host.tools.list()
            assert spec.side_effects == 'unknown' and spec.execution_mode == 'sequential'
            sid = await app.host.sessions.create('A')
            result = await app.host.runs.start(sid, [('user', 'MCP')])
            if action in ('approve', 'reject'):
                assert not calls(path)
                assert result['approval_requests'][0]['remember_scope'] == 'tool'
                prepared = result['tool_execution']['calls'][0]['subject']
                assert prepared['mcp']['server_id'] == 'local' and prepared['args'] == args
                result = await app.host.runs.resume_approval(sid, [action])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            success = action in ('approve', 'allow', 'BypassApprovals')
            assert output.status == ('success' if success else 'error')
            assert output.tool_call_id == 't' and len(calls(path)) == int(success)
            assert any(isinstance(m, ToolMessage) and m.tool_call_id == 't' for m in model.seen[-1])
            assert any(e.event_type == 'tool_call_finished' for e in events)
            if success:
                assert output.artifact['structured_content']['count'] == 1
                assert output.artifact['source'] == {'server_id': 'local', 'remote_tool': 'echo'}
        finally:
            await app.aclose()
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())


def test_session_remembered_tool_changes_args_reuses_client_but_other_session_asks(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'remember.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {'text': 'first'}, 'a'), AIMessage(content='one'),
            call(NAME, {'text': 'second'}, 'b'), AIMessage(content='two'), call(NAME, {}, 'c')])
        try:
            sid = await app.host.sessions.create('A')
            result = await app.host.runs.start(sid, [('user', 'first')])
            assert result['approval_requests']
            await app.host.runs.resume_approval(sid, ['approve'], remember_approved=['a'])
            result = await app.host.runs.start(sid, [('user', 'second')])
            assert not result.get('approval_requests')
            assert [r['count'] for r in calls(path)] == [1, 2]
            assert len({r['pid'] for r in calls(path)}) == 1
            other = await app.host.sessions.create('A')
            result = await app.host.runs.start(other, [('user', 'other')])
            assert result['approval_requests'] and len(calls(path)) == 2
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('action', ['drop', 'wait'])
def test_failure_or_stop_never_replays_business_call(monkeypatch, tmp_path, action):
    async def scenario():
        path = tmp_path / 'failure.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [call(NAME, {'text': action}), AIMessage(content='failure handled')], mode='BypassApprovals')
        try:
            sid = await app.host.sessions.create('A')
            task = asyncio.create_task(app.host.runs.start(sid, [('user', action)]))
            if action == 'wait':
                await until(lambda: bool(calls(path)))
                app.host.runs.stop(sid)
            result = await asyncio.wait_for(task, 15)
            assert len(calls(path)) == 1
            if action == 'wait':
                assert result['run_stopped']
            else:
                output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
                assert output.status == 'error' and 'may have occurred' in output.content
        finally:
            await app.aclose()
    asyncio.run(scenario())
