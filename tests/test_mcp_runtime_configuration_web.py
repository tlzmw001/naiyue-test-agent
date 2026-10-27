"""The existing authenticated Web surface refreshes only the local MCP config."""

import asyncio
import json

from langchain_core.messages import AIMessage

from naiyue_test_agent import config_paths
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls
from test_mcp_connections import stdio_settings
from test_mcp_runtime_configuration import write_config
from test_web_api import request, web_client
from test_web_tools import approval_request


def test_web_add_refresh_select_approve_disable_and_reenable(monkeypatch, tmp_path):
    async def scenario():
        monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: tmp_path)
        config, audit = tmp_path / 'settings.toml', tmp_path / 'web.jsonl'
        server = stdio_settings(audit, 'stateful', allowed_tools=['echo'])
        model = StreamingToolModel(replies=[call(NAME, {}), AIMessage(content='done')])
        async with web_client(monkeypatch, tmp_path, model, runtime_settings=AppSettings(), workspace_root=tmp_path) as (client, web):
            write_config(config, {'local': server})
            assert (await client.post('/api/v1/scopes/chats/mcp/refresh', headers={'Authorization': ''})).status_code == 401
            assert (await client.post('/api/v1/scopes/chats/mcp/refresh', headers={'Origin': 'https://evil.example'})).status_code == 403
            response = await client.post('/api/v1/scopes/chats/mcp/refresh')
            assert response.status_code == 200
            assert response.json()['servers'][0]['status'] == 'enabled' and not calls(audit) and model.seen == []
            options = (await client.get('/api/v1/scopes/chats/session-controls')).json()
            assert next(t for t in options['tools'] if t['name'] == NAME)['available']
            assert options['mcp_servers'][0]['enabled']
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}'
            assert (await client.patch(endpoint + '/tools', json={'active_tools': [NAME]})).status_code == 200
            run = (await client.post(endpoint + '/runs', json=request('call new MCP'))).json()['run_id']
            await web.state.delivery.wait(run)
            batch = (await client.get(endpoint)).json()['approval_batches'][0]
            response = await client.post(endpoint + '/approvals', json=approval_request(batch, ['approve']))
            await web.state.delivery.wait(response.json()['run_id'])
            assert len(calls(audit)) == 1
            write_config(config, {'local': server.model_copy(update={'enabled': False})})
            assert (await client.post('/api/v1/scopes/chats/mcp/refresh')).json()['servers'][0]['status'] == 'disabled'
            options = (await client.get('/api/v1/scopes/chats/session-controls')).json()
            assert next(t for t in options['tools'] if t['name'] == NAME)['available'] is False
            assert (await client.get(endpoint)).json()['active_tools'] == [NAME]
            write_config(config, {'local': server})
            assert (await client.post('/api/v1/scopes/chats/mcp/refresh')).json()['servers'][0]['status'] == 'enabled'
            assert next(t for t in (await client.get('/api/v1/scopes/chats/session-controls')).json()['tools'] if t['name'] == NAME)['available']
            assert 'config_identity' not in json.dumps(options)
    asyncio.run(scenario())


def test_refresh_invalid_file_is_safe_and_does_not_accept_a_web_path(monkeypatch, tmp_path):
    async def scenario():
        monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: tmp_path)
        async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[]), runtime_settings=AppSettings()) as (client, _):
            response = await client.post('/api/v1/scopes/chats/mcp/refresh')
            assert response.status_code == 422
            assert response.json()['error']['code'] == 'mcp_settings_invalid'
            (tmp_path / 'settings.toml').write_text('PRIVATE-NOT-TOML')
            response = await client.post('/api/v1/scopes/chats/mcp/refresh')
            assert response.status_code == 422 and 'PRIVATE' not in response.text
            assert (await client.post('/api/v1/scopes/chats/mcp/refresh', json={'path': '/PRIVATE'})).status_code == 422
    asyncio.run(scenario())
