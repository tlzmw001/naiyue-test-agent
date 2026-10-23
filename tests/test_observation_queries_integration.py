"""Native SDK facts reach existing SQLite, HTTP queries and terminal views."""

import asyncio
import json

import httpx
import pytest

from native_application_helpers import async_test
from http_observation_helpers import sdk_catalog, sdk_response
from naiyue_test_agent import application
from naiyue_test_agent.cli.cli_async import AsyncCliApplication
from naiyue_test_agent.cli.cli_observation import handle_observation_command
from test_web_api import web_client, request


@pytest.mark.parametrize('network_error', [False, True])
@async_test
async def test_native_sdk_retries_survive_web_pagination_and_cold_read(monkeypatch, tmp_path, network_error):
    sent = []
    def serve(req):
        sent.append(True)
        if len(sent) < 3:
            if network_error:
                raise httpx.ConnectError('PRIVATE_NETWORK', request=req)
            return httpx.Response(503, headers={'retry-after-ms': '1'}, json={'error': {'message': 'offline'}})
        return sdk_response(req, 'openai')
    async with sdk_catalog(serve=serve) as catalog:
        async with web_client(monkeypatch, tmp_path, catalog.get('A'), diagnostic_page_size=1) as (client, web):
            app = web.state.service.application
            # web_client creates its own catalog and therefore its own clients.
            # Stub only those actual owned transports, keeping their hooks/options.
            for owned, _ in app.models._catalog._owned_http_clients:
                owned._transport = httpx.MockTransport(serve)
                owned._mounts = {}
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            rid = (await client.post(f'/api/sessions/{sid}/runs', json=request('offline observation'))).json()['run_id']
            await web.state.delivery.wait(rid)
            assert web.state.delivery.get(rid).status == 'completed'
            await app.observability._diagnostics.aclose()
            usage = (await client.get(f'/api/sessions/{sid}/usage')).json()
            assert usage['recorded_calls'] == 1 and usage['duration']['reported_calls'] == 1
            rows = (await client.get(f'/api/sessions/{sid}/diagnostics')).json()['runs']
            assert rows[0]['duration_ms'] >= 0
            expected = {'status': 'complete', 'observed_attempts': 3,
                        'response_headers': 1 if network_error else 3, 'unconfirmed': 2 if network_error else 0}
            assert rows[0]['http_attempts'] == expected
            events, after = [], 0
            while True:
                page = (await client.get(f'/api/sessions/{sid}/diagnostics/{rid}', params={'after': after})).json()
                assert page['summary']['http_attempts'] == expected
                events.extend(page['events'])
                if page['next_after'] is None:
                    break
                after = page['next_after']
            assert sum(e['event_type'] == 'model_http_attempt_started' for e in events) == 3
            models = [e for e in events if e['event_type'] == 'model_call_started']
            assert len(models) == 1
            assert {e['data']['invocation_id'] for e in events if e['event_type'].startswith('model_http')} == {
                models[0]['data']['invocation_id']}
            before = app.sessions._persistence.storage.database.connection.total_changes
            assert (await client.get(f'/api/sessions/{sid}/usage')).status_code == 200
            assert app.sessions._persistence.storage.database.connection.total_changes == before and len(sent) == 3
            assert 'PRIVATE_NETWORK' not in json.dumps([usage, rows, events])


@pytest.mark.parametrize('disk', [False, True])
@async_test
async def test_terminal_bridge_queries_native_saved_facts_without_additional_provider_calls(monkeypatch, tmp_path, disk):
    sent = []
    def serve(req):
        sent.append(True)
        return sdk_response(req, 'openai')
    async with sdk_catalog(serve=serve) as catalog:
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
        bridge = AsyncCliApplication('unused', tools=[], system_prompt='offline', response_format=None,
                                     database_path=tmp_path / 'state.sqlite' if disk else None)
        try:
            sid = bridge.call(lambda api: api.sessions.create('A'))
            assert bridge.call(lambda api: api.runs.start(sid, [('user', 'offline task')]))['messages'][-1].content == 'done'
            bridge._submit(bridge._app.observability._diagnostics.aclose)
            runs = bridge.call(lambda api: api.observability.runs(sid))
            rid = runs['runs'][0]['run_id']
            statements = []
            bridge._app.sessions._persistence.storage.database.connection.set_trace_callback(statements.append)
            lines = []
            for command in (':usage', ':diagnostics', ':diagnostics ' + rid):
                handle_observation_command(bridge, sid, command, lines.append)
            assert len(sent) == 1 and '已记录 HTTP 尝试 1' in '\n'.join(lines)
            assert '已记录模型调用耗时' in '\n'.join(lines) and 'HTTP 200' in '\n'.join(lines)
            assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE')) for s in statements)
        finally:
            bridge.close()
