"""Browser selections use real session storage and the original permission batch."""

import asyncio
import pytest

from langchain_core.messages import AIMessage

from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel
from test_web_api import request, web_client
from test_web_tools import calls


@pytest.mark.parametrize('names', [[], ['powershell', 'read']])
def test_configured_default_tools_only_apply_to_new_sessions(monkeypatch, tmp_path, names):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        model = StreamingToolModel(replies=[])
        settings = AppSettings.model_validate({'default_active_tools': names})
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root, runtime_settings=settings) as (client, app):
            assert (await client.get('/api/v1/scopes/chats/session-controls')).json()['default_active_tools'] == names
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['active_tools'] == names
            response = await client.patch(f'/api/v1/scopes/chats/sessions/{sid}/tools', json={'active_tools': ['grep']})
            assert response.json() == {'active_tools': ['grep']}
        changed = AppSettings.model_validate({'default_active_tools': ['find']})
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root, runtime_settings=changed) as (client, app):
            assert (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/prepare')).status_code == 200
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['active_tools'] == ['grep']
            new = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            assert (await client.get(f'/api/v1/scopes/chats/sessions/{new}')).json()['active_tools'] == ['find']
        assert model.seen == []
    asyncio.run(scenario())


@pytest.mark.parametrize('rootless', [False, True])
def test_default_tool_validation_and_rootless_web_boundary(monkeypatch, tmp_path, rootless):
    async def scenario():
        settings = AppSettings.model_validate({'default_active_tools': ['read' if rootless else 'unknown']})
        options = {} if rootless else {'workspace_root': tmp_path}
        if rootless:
            async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), runtime_settings=settings) as (client, app):
                assert (await client.get('/api/v1/scopes/chats/session-controls')).json()['default_active_tools'] == []
        else:
            with pytest.raises(ValueError, match='Unknown tool name'):
                async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), runtime_settings=settings, **options):
                    raise AssertionError('Unknown tool must fail startup')
    asyncio.run(scenario())


@pytest.mark.parametrize('valid', [True, False])
def test_configured_mcp_default_is_validated_after_discovery_and_still_requires_approval(monkeypatch, tmp_path, valid):
    from test_async_application import call
    from test_mcp_connections import facts, process_alive, stdio_settings
    async def scenario():
        audit = tmp_path / 'mcp.jsonl'
        settings = AppSettings.model_validate({
            'default_active_tools': ['mcp__local__echo' if valid else 'mcp__local__missing'],
            'mcp': {'servers': {'local': stdio_settings(audit, 'stateful', allowed_tools=['echo']).model_dump()}}})
        model = StreamingToolModel(replies=[call('mcp__local__echo', {'text': 'hello'})])
        if not valid:
            with pytest.raises(ValueError, match='Unknown tool name'):
                async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path, runtime_settings=settings):
                    raise AssertionError('Unknown MCP default must fail startup')
        else:
            async with web_client(monkeypatch, tmp_path, model, workspace_root=tmp_path, runtime_settings=settings) as (client, app):
                options = (await client.get('/api/v1/scopes/chats/session-controls')).json()
                assert options['default_active_tools'] == ['mcp__local__echo']
                sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
                run = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('call MCP'))).json()['run_id']
                await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
                assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['approval_batches']
        assert not [row for row in facts(audit) if row['event'] == 'call']
        assert all(not process_alive(row['pid']) for row in facts(audit) if row['event'] == 'start')
    asyncio.run(scenario())


def test_control_options_are_authenticated_and_expose_only_public_metadata(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            endpoint = '/api/v1/scopes/chats/session-controls'
            assert (await client.get(endpoint, headers={'Authorization': ''})).status_code == 401
            response = await client.get(endpoint)
            assert response.status_code == 200
            value = response.json()
            assert value['permission_modes'] == ['Default', 'NoAsk', 'BypassApprovals']
            assert value['default_permission_mode'] == 'Default'
            assert 'bash' not in value['default_active_tools']
            assert {'read', 'bash', 'powershell', 'subagent'} <= {row['name'] for row in value['tools']}
            assert all(set(row) == {'name', 'description'} and row['description'] for row in value['tools'])
            assert (await client.get('/api/v1/scopes/chats/sessions')).json() == []
            assert model.seen == []
    asyncio.run(scenario())


def test_tool_and_mode_selection_are_session_local_and_survive_reopen(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        model = StreamingToolModel(replies=[])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            first, second = [(await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id'] for _ in range(2)]
            endpoint = f'/api/v1/scopes/chats/sessions/{first}'
            assert (await client.patch(endpoint + '/tools', json={'active_tools': ['read', 'bash']})).json() == {'active_tools': ['read', 'bash']}
            assert (await client.patch(endpoint + '/permission-mode', json={'permission_mode': 'NoAsk'})).json() == {'permission_mode': 'NoAsk'}
            untouched = (await client.get(f'/api/v1/scopes/chats/sessions/{second}')).json()
            assert untouched['permission_mode'] == 'Default' and 'bash' not in untouched['active_tools']
            assert model.seen == []
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            assert (await client.post(endpoint + '/prepare')).status_code == 200
            restored = (await client.get(endpoint)).json()
            assert restored['active_tools'] == ['read', 'bash'] and restored['permission_mode'] == 'NoAsk'
            assert (await client.patch(endpoint + '/tools', json={'active_tools': []})).json() == {'active_tools': []}
            assert (await client.get(endpoint)).json()['active_tools'] == []
            assert model.seen == []
    asyncio.run(scenario())


def test_invalid_or_unavailable_selections_do_not_change_state(monkeypatch, tmp_path):
    async def scenario():
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, app):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            for body in ({'active_tools': ['UNKNOWN_PRIVATE_NAME']}, {'active_tools': ['read', 'read']},
                         {'active_tools': 'read'}, {'active_tools': [], 'grant': True}):
                response = await client.patch(endpoint + '/tools', json=body)
                assert response.status_code == 422 and 'UNKNOWN_PRIVATE_NAME' not in response.text
            for body in ({'permission_mode': 'Auto'}, {'permission_mode': 'unknown'}, {'permission_mode': 'Default', 'extra': 1}):
                assert (await client.patch(endpoint + '/permission-mode', json=body)).status_code == 422
            assert (await client.patch(endpoint + '/tools', json={'active_tools': []}, headers={'Authorization': ''})).status_code == 401
            assert (await client.patch(endpoint + '/permission-mode', json={'permission_mode': 'NoAsk'}, headers={'Origin': 'https://evil.example'})).status_code == 403
            view = (await client.get(endpoint)).json()
            assert view['active_tools'] == [] and view['permission_mode'] == 'Default'
            assert (await client.patch('/api/v1/scopes/chats/sessions/missing/tools', json={'active_tools': []})).status_code == 404
    asyncio.run(scenario())


def test_options_follow_configured_default_and_auto_availability(monkeypatch, tmp_path):
    async def scenario():
        settings = AppSettings.model_validate({'permissions': {'mode': 'NoAsk', 'auto': {
            'fast_model_id': 'A', 'detailed_model_id': 'A'}}})
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), runtime_settings=settings) as (client, app):
            options = (await client.get('/api/v1/scopes/chats/session-controls')).json()
            assert options['default_permission_mode'] == 'NoAsk'
            assert options['permission_modes'] == ['Default', 'NoAsk', 'Auto', 'BypassApprovals']
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            assert (await client.patch(f'/api/v1/scopes/chats/sessions/{sid}/permission-mode', json={'permission_mode': 'Auto'})).json() == {'permission_mode': 'Auto'}
    asyncio.run(scenario())


def test_changes_do_not_authorize_or_remove_an_existing_approval(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('never read before approval')
        model = StreamingToolModel(replies=[calls(('read', {'path': str(outside)}, 'read-1')), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            run = (await client.post(endpoint + '/runs', json=request('read outside'))).json()['run_id']
            await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            original = (await client.get(endpoint)).json()['approval_batches']
            assert original and len(model.seen) == 1
            assert (await client.patch(endpoint + '/permission-mode', json={'permission_mode': 'BypassApprovals'})).status_code == 200
            assert (await client.patch(endpoint + '/tools', json={'active_tools': []})).status_code == 200
            after = (await client.get(endpoint)).json()
            assert after['approval_batches'] == original and after['pending_approval']
            assert after['permission_mode'] == 'BypassApprovals' and after['active_tools'] == []
            assert len(model.seen) == 1
            assert not any(row['type'] == 'tool' for row in after['messages'])
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), workspace_root=root) as (client, app):
            assert (await client.post(endpoint + '/prepare')).status_code == 200
            assert 'recovery' in (await client.get(endpoint)).json()
            assert (await client.patch(endpoint + '/tools', json={'active_tools': ['read']})).status_code == 409
            assert (await client.patch(endpoint + '/permission-mode', json={'permission_mode': 'Default'})).status_code == 409
    asyncio.run(scenario())
