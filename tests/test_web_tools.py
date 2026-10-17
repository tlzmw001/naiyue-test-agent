"""HTTP tooling reuses real built-in tools, HITL and persistent async execution."""

import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from test_async_application import StreamingToolModel
from test_web_api import web_client, request
from naiyue_test_agent.web.settings import WebSettings


def calls(*items):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': identity}
        for name, args, identity in items], response_metadata={'finish_reason': 'tool_calls'})


async def start(client, text='use tools'):
    sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
    run = (await client.post(f'/api/sessions/{sid}/runs', json=request(text))).json()['run_id']
    await client.get(f'/api/runs/{run}/events')
    return sid, run


def test_web_workspace_read_and_tool_projection(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        (root / 'sample.txt').write_text('actual tool result')
        model = StreamingToolModel(replies=[calls(('read', {'path': 'sample.txt'}, 'read-1')), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            sid, run = await start(client)
            view = (await client.get(f'/api/runs/{run}')).json()
            assert view['status'] == 'completed'
            assert view['tool_states']['read-1']['outcome'] == 'success'
            ai = next(m for m in view['messages'] if m.get('tool_calls'))
            result = next(m for m in view['messages'] if m['type'] == 'tool')
            assert ai['tool_calls'][0]['args'] == {'path': 'sample.txt'}
            assert result['tool_call_id'] == 'read-1' and result['content'] == 'actual tool result'
            assert result['status'] == 'success'
    asyncio.run(scenario())


def test_web_tool_error_returns_to_model_without_failing_run(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        model = StreamingToolModel(replies=[calls(('read', {'path': 'missing.txt'}, 'missing')), AIMessage(content='handled')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            _, run = await start(client)
            view = (await client.get(f'/api/runs/{run}')).json()
            assert view['status'] == 'completed'
            assert view['tool_states']['missing']['outcome'] == 'error_result'
            returned = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
            assert returned.status == 'error' and 'not' in returned.content.lower()
    asyncio.run(scenario())


def test_web_approval_matches_batch_and_resumes_exactly_once(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        first, second = tmp_path / 'first.txt', tmp_path / 'second.txt'
        first.write_text('approved content')
        second.write_text('rejected content')
        model = StreamingToolModel(replies=[calls(('read', {'path': str(first)}, 'one'),
            ('read', {'path': str(second)}, 'two')), AIMessage(content='continued')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            sid, origin = await start(client)
            value = (await client.get(f'/api/sessions/{sid}')).json()
            assert value['approval_run_id'] == origin and value['pending_approval']
            assert [item['tool_call_id'] for item in value['approval_requests']] == ['one', 'two']
            assert value['approval_requests'][0]['target'] == str(first)
            assert len(model.seen) == 1
            endpoint = f'/api/sessions/{sid}/approvals'
            body = {'request_id': str(uuid4()), 'origin_run_id': origin,
                'decisions': [{'tool_call_id': 'one', 'decision': 'approve'}, {'tool_call_id': 'two', 'decision': 'reject'}]}
            for invalid in ({**body, 'origin_run_id': 'wrong'}, {**body, 'decisions': body['decisions'][:1]},
                            {**body, 'decisions': list(reversed(body['decisions']))}):
                assert (await client.post(endpoint, json=invalid)).status_code == 409
            first_resume = await client.post(endpoint, json=body)
            assert first_resume.status_code == 202
            resumed = first_resume.json()['run_id']
            assert resumed != origin
            await client.get(f'/api/runs/{resumed}/events')
            repeated = await client.post(endpoint, json=body)
            assert repeated.json() == first_resume.json()
            returned = {m.tool_call_id: m for m in model.seen[1] if isinstance(m, ToolMessage)}
            assert set(returned) == {'one', 'two'}
            assert returned['one'].content == 'approved content' and returned['two'].status == 'error'
            assert 'rejected content' not in returned['two'].content
            view = (await client.get(f'/api/sessions/{sid}')).json()
            assert view['pending_approval'] is False and view['can_run'] is True and len(model.seen) == 2
            assert (await client.post(endpoint, json={**body, 'request_id': str(uuid4())})).status_code == 409
    asyncio.run(scenario())


def test_web_restored_tool_messages_preserve_association(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        (root / 'file.txt').write_text('saved tool output')
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[calls(('read', {'path': 'file.txt'}, 't')), AIMessage(content='done')]), workspace_root=root) as (client, app):
            sid, _ = await start(client)
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=root) as (client, app):
            messages = (await client.get(f'/api/sessions/{sid}')).json()['messages']
            assert next(m for m in messages if m['type'] == 'tool')['tool_call_id'] == 't'
            assert next(m for m in messages if m.get('tool_calls'))['tool_calls'][0]['id'] == 't'
    asyncio.run(scenario())


def test_web_rejects_removed_shell_setting(tmp_path):
    with pytest.raises(ValidationError):
        WebSettings(config_path='unused', database_path=tmp_path / 'db', frontend_dist=tmp_path, host_bash=True)


def test_all_file_tools_are_wired_through_web(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        model = StreamingToolModel(replies=[calls(('write', {'path': 'new.txt', 'content': 'before'}, 'write'),
            ('edit', {'path': 'new.txt', 'edits': [{'oldText': 'before', 'newText': 'after'}]}, 'edit'),
            ('read', {'path': 'new.txt'}, 'read'), ('ls', {'path': '.'}, 'ls'),
            ('find', {'pattern': '*.txt'}, 'find'), ('grep', {'pattern': 'after', 'path': 'new.txt'}, 'grep')),
            AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            _, run = await start(client)
            value = (await client.get(f'/api/runs/{run}')).json()
            assert value['status'] == 'completed' and (root / 'new.txt').read_text() == 'after'
            assert set(value['tool_states']) == {'write', 'edit', 'read', 'ls', 'find', 'grep'}
            assert all(row['outcome'] == 'success' for row in value['tool_states'].values())
            returned = {m.tool_call_id: m.content for m in model.seen[1] if isinstance(m, ToolMessage)}
            assert returned['read'] == 'after' and 'new.txt' in returned['find'] and 'after' in returned['grep']
    asyncio.run(scenario())


def test_web_bash_opt_in_approval_and_safe_command(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        model = StreamingToolModel(replies=[calls(('bash', {'command': 'printf WEB-BASH'}, 'bash')), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            backend = app.state.service.application
            assert 'bash' in [spec.tool.name for spec in backend.get_registered_tools()]
            assert 'bash' not in backend.get_active_tools(sid)
            assert 'powershell' in [spec.tool.name for spec in backend.get_registered_tools()]
            assert set(backend.get_active_tools(sid)) == {'read', 'ls', 'find', 'grep', 'write', 'edit'}
            await backend.aset_active_tools(sid, ['bash'])
            assert (await client.get(f'/api/sessions/{sid}')).json()['active_tools'] == ['bash']
            origin = (await client.post(f'/api/sessions/{sid}/runs', json=request('use shell'))).json()['run_id']
            await client.get(f'/api/runs/{origin}/events')
            value = (await client.get(f'/api/sessions/{sid}')).json()
            assert value['approval_requests'][0]['target'] == 'printf WEB-BASH'
            response = await client.post(f'/api/sessions/{sid}/approvals', json={
                'request_id': str(uuid4()), 'origin_run_id': origin,
                'decisions': [{'tool_call_id': 'bash', 'decision': 'approve'}]})
            response.raise_for_status()
            await client.get(f'/api/runs/{response.json()["run_id"]}/events')
            result = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
            assert result.status == 'success' and 'WEB-BASH' in result.content
    asyncio.run(scenario())


def test_approval_request_validation_auth_and_restart_block(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('not read before approval')
        model = StreamingToolModel(replies=[calls(('read', {'path': str(outside)}, 't'))])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            sid, origin = await start(client)
            endpoint = f'/api/sessions/{sid}/approvals'
            body = {'request_id': str(uuid4()), 'origin_run_id': origin,
                'decisions': [{'tool_call_id': 't', 'decision': 'approve'}]}
            assert (await client.post(endpoint, json=body, headers={'Authorization': ''})).status_code == 401
            assert (await client.post(endpoint, json={**body, 'grant': 'PRIVATE_GRANT'})).status_code == 422
            invalid = await client.post(endpoint, json={**body, 'decisions': [{'tool_call_id': 't', 'decision': 'edit'}]})
            assert invalid.status_code == 422 and len(model.seen) == 1
        next_model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, next_model, workspace_root=root) as (client, app):
            assert (await client.post(endpoint, json=body)).status_code == 409
            view = (await client.get(f'/api/sessions/{sid}')).json()
            assert view['recovery']['kind'] == 'pending_approval'
            assert not view['can_run'] and not view['pending_approval']
            assert view['approval_requests'] == [] and view['live'] is None
            assert view['recovery']['unconfirmed_tool_call_ids'] == ['t']
            assert next_model.seen == []
    asyncio.run(scenario())


def test_tool_outcome_keeps_run_origin_after_approval_and_next_chat(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('must not be read')
        model = StreamingToolModel(replies=[calls(('read', {'path': str(outside)}, 't')),
            AIMessage(content='rejection handled'), AIMessage(content='next chat')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            sid, origin = await start(client)
            response = await client.post(f'/api/sessions/{sid}/approvals', json={
                'request_id': str(uuid4()), 'origin_run_id': origin,
                'decisions': [{'tool_call_id': 't', 'decision': 'reject'}]})
            resumed = response.json()['run_id']
            await client.get(f'/api/runs/{resumed}/events')
            next_run = (await client.post(f'/api/sessions/{sid}/runs', json=request('another'))).json()['run_id']
            await client.get(f'/api/runs/{next_run}/events')
            view = (await client.get(f'/api/runs/{next_run}')).json()
            result = next(m for m in view['messages'] if m['type'] == 'tool')
            assert result['run_id'] == resumed
            assert view['tool_states']['t']['run_id'] == resumed
            assert view['tool_states']['t']['outcome'] == 'not_executed'
            owner = next(m for m in view['messages'] if m.get('tool_calls'))
            assert owner['run_id'] == origin
    asyncio.run(scenario())
