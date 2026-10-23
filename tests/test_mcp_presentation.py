"""MCP display facts follow genuine execution and survive bounded history reads."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.agent_runtime.message_observation import _snapshot
from naiyue_test_agent.cli.cli_display import CliEventDisplay
from naiyue_test_agent.web.chat_history import record_view
from naiyue_test_agent.web.serialization import message_view
from naiyue_test_agent.settings import AppSettings
from test_async_application import call
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import stdio_settings
from test_mcp_recovery import opened


@pytest.mark.parametrize('action,outcome', [('normal', 'success'), ('error', 'tool_error'),
    ('media', 'unsupported_result'), ('interaction', 'unsupported_result'), ('drop', 'outcome_unknown'),
    ('reject', 'not_executed'), ('invalid', 'not_executed'), ('drift', 'not_executed')])
def test_graph_projects_real_source_and_outcome(monkeypatch, tmp_path, action, outcome, caplog):
    async def scenario():
        path = tmp_path / 'display.jsonl'
        args = {'text': 3 if action == 'invalid' else action}
        app, model = await make_mcp_app(monkeypatch, path, [call(NAME, args), AIMessage(content='done')],
            mode='Default' if action in ('reject', 'drift') else 'BypassApprovals',
            server=stdio_settings(path, 'stateful' if action == 'drop' else 'tools', allowed_tools=['echo']))
        events = []; app.subscribe_events(events.append)
        try:
            sid = await app.acreate_workspace_session('A', tmp_path)
            result = await app.arun(sid, [('user', 'check')])
            if action in ('reject', 'drift'):
                if action == 'drift':
                    path.with_suffix('.drift').touch()
                pending = app.pending_approval_batches(sid)[0]
                assert pending['items'][0]['mcp_source'] == {'server_id': 'local', 'remote_tool': 'echo'}
                approval = next(e for e in events if e.event_type == 'approval_requested')
                assert approval.data.items[0].mcp_source == pending['items'][0]['mcp_source']
                result = await app.aresume_tool_approval(sid, ['reject' if action == 'reject' else 'approve'])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            view = message_view(output)['mcp_result']
            assert view['source']['server_id'] == 'local' and view['source']['remote_tool'] == 'echo'
            assert view['outcome'] == outcome
            snapshot = json.loads(_snapshot(output, 'graph_values').snapshot_json)
            assert snapshot['mcp_result'] == view
            observed = [json.loads(e.data.snapshot_json) for e in events if e.event_type == 'message_completed']
            assert any(row.get('mcp_result') == view for row in observed)
            assert len(calls(path)) == int(action not in ('reject', 'invalid', 'drift'))
            assert any(isinstance(m, ToolMessage) for m in model.seen[-1])
            lines = []; display = CliEventDisplay(sid, writer=lines.append)
            display._message(snapshot); display._message(snapshot)
            assert sum('MCP local/echo' in line for line in lines) == 1
            assert 'PRIVATE' not in json.dumps(view) + caplog.text
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_projection_is_whitelisted_and_does_not_guess_from_tool_name():
    from naiyue_test_agent.mcp_client.presentation import result_projection
    artifact = {'type': 'mcp_result', 'version': 1, 'source': {'server_id': 'local', 'remote_tool': 'echo',
        'url': 'PRIVATE_URL'}, 'transport': 'stdio', 'outcome': 'success', 'truncated': True,
        'result_bytes': 9999, 'archive_complete': False, 'archive_reason': 'command_limit',
        'archive_paths': {'raw': 'naiyue-agent-output/' + 'a' * 32 + '-raw.log',
                          'text': '../PRIVATE', 'token': 'PRIVATE'}, 'structured_content': {'token': 'PRIVATE'},
        'config_identity': 'PRIVATE', 'headers': {'Authorization': 'PRIVATE'}}
    view = result_projection(artifact, name=NAME)
    assert view == {'version': 1, 'source': {'server_id': 'local', 'remote_tool': 'echo', 'transport': 'stdio'},
        'outcome': 'success', 'truncated': True, 'result_bytes': 9999, 'archive_complete': False,
        'archive_reason': 'command_limit', 'archive_paths': {'raw': 'naiyue-agent-output/' + 'a' * 32 + '-raw.log'}}
    assert result_projection(None, name=NAME) is None
    assert result_projection(artifact, name='read') is None
    assert result_projection({**artifact, 'version': 2}, name=NAME) is None
    legacy = {k: v for k, v in artifact.items() if k not in ('type', 'version', 'outcome')}
    legacy['is_error'] = True
    assert result_projection(legacy, name=NAME)['outcome'] == 'tool_error'


def test_catalog_uses_permission_source_and_explicit_child_role(monkeypatch, tmp_path):
    from naiyue_test_agent.web.service import WebService
    from test_mcp_subagents import MCPChildModel, make_child_app
    async def scenario():
        app, _ = await make_child_app(monkeypatch, tmp_path / 'catalog.jsonl', MCPChildModel(replies=[]))
        try:
            service = object.__new__(WebService)
            service.application, service.settings = app, SimpleNamespace(max_frame_bytes=100000)
            value = service.session_control_options()
            row = next(t for t in value['tools'] if t['name'] == NAME)
            assert row['mcp_source'] == {'server_id': 'local', 'remote_tool': 'echo', 'transport': 'stdio'}
            assert row['subagent_roles'] == ['tester']
            builtin = next(t for t in value['tools'] if t['name'] == 'subagent')
            assert set(builtin) == {'name', 'description'}
            assert 'config_identity' not in json.dumps(value) and 'headers' not in json.dumps(value)
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('budget', [512, 65536])
def test_long_result_sql_preview_and_reopen_match_live_projection(monkeypatch, tmp_path, budget):
    async def scenario():
        path, database = tmp_path / 'saved.jsonl', tmp_path / 'saved.sqlite'
        settings = AppSettings.model_validate({'mcp': {'servers': {'local': stdio_settings(path, 'tools',
            allowed_tools=['echo'], max_result_bytes=budget).model_dump()}}})
        async with opened(monkeypatch, path, database, [call(NAME, {'text': 'large'}), AIMessage(content='done')], settings=settings) as (app, _):
            sid = await app.acreate_workspace_session('A', tmp_path)
            await app.arun(sid, [('user', 'large result')])
            result = await app.aresume_tool_approval(sid, ['approve'])
            output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            expected = message_view(output)['mcp_result']
            assert expected['outcome'] == 'success'
            assert expected['truncated'] == (budget == 512)
            assert expected['archive_complete'] == (budget == 512)
            if budget == 512:
                assert expected['archive_paths']['raw'] == output.artifact['archive_paths']['raw']
                assert (tmp_path / expected['archive_paths']['raw']).is_file()
            page = await app.aget_chat_page(sid, limit=20, body_bytes=100, preview_chars=20)
            saved = next(r for r in page['records'] if r['message_type'] == 'tool')
            assert saved['body_json'] is None
            assert len(saved['mcp_artifact_json'].encode()) <= 4096
            assert record_view(saved)['mcp_result'] == expected
        async with opened(monkeypatch, path, database, [], settings=settings) as (app, _):
            page = await app.aget_chat_page(sid, limit=20, body_bytes=100, preview_chars=20)
            saved = next(r for r in page['records'] if r['message_type'] == 'tool')
            assert record_view(saved)['mcp_result'] == expected
        assert len(calls(path)) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('key,value', [('outcome', []), ('archive_reason', {}), ('source', []),
    ('transport', {}), ('archive_paths', []), ('result_bytes', True), ('truncated', 1)])
def test_malformed_artifact_never_breaks_message_projection(key, value):
    artifact = {'type': 'mcp_result', 'version': 1, 'source': {'server_id': 'local', 'remote_tool': 'echo'},
                'outcome': 'success', key: value}
    row = message_view(ToolMessage(id='m', content='literal', name=NAME, tool_call_id='c', artifact=artifact))
    assert row['content'] == 'literal'
    assert 'PRIVATE' not in json.dumps(row.get('mcp_result'))


def test_cli_revised_result_metadata_and_readonly_history(monkeypatch, tmp_path):
    artifact = {'type': 'mcp_result', 'version': 1, 'source': {'server_id': 'local', 'remote_tool': 'echo'},
                'outcome': 'outcome_unknown'}
    message = ToolMessage(id='m', content='same', status='error', name=NAME, tool_call_id='c', artifact=artifact)
    lines = []; display = CliEventDisplay('s', writer=lines.append)
    display._message(message_view(message))
    display._message(message_view(message.model_copy(update={'artifact': {**artifact, 'outcome': 'not_executed'}})))
    assert any('未执行' in line for line in lines)
    display.show_history({'messages': [message], 'recovery': {'kind': 'completed', 'reason': 'saved',
                                                            'unconfirmed_tool_call_ids': []}})
    assert sum('结果未知' in line for line in lines) == 2
