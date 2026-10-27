"""The future CLI uses authenticated HTTP, never borrows Python runtime objects."""
import asyncio
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.service.client import ServiceClient
from naiyue_test_agent.service.settings import ServiceError
from test_async_application import StreamingToolModel
from test_service_protocol import service_clients


def test_two_protocol_clients_connect_send_and_read_without_owning_service(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='answer')])) as (_, _, app, options):
            transport = httpx.ASGITransport(app=app)
            a = await ServiceClient.connect(options, transport=transport)
            b = await ServiceClient.connect(options, transport=transport)
            try:
                assert a.instance_id == b.instance_id
                sid = (await a.request('POST', '/api/v1/scopes/chats/sessions', json={'model_id': 'A'}))['session_id']
                body = {'request_id': str(uuid4()), 'text': 'question'}
                result = await a.request('POST', f'/api/v1/scopes/chats/sessions/{sid}/runs', json=body)
                events = [e async for e in b.events('chats', result['run_id'])]
                assert any(e['data'].get('event_type') == 'run_finished' for e in events)
                sequence = [int(e['id']) for e in events]
                assert sequence == sorted(set(sequence))
                assert [e async for e in a.events('chats', result['run_id'], after=sequence[-1])] == []
                await a.aclose()
                rows = await b.request('GET', '/api/v1/scopes/chats/sessions')
                assert len(rows) == 1 and rows[0]['session_id'] == sid
            finally:
                await a.aclose()
                await b.aclose()
    asyncio.run(scenario())


def test_client_refuses_config_mismatch_and_arbitrary_urls(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (_, _, app, options):
            transport = httpx.ASGITransport(app=app)
            different = options.model_copy(update={'config_path': tmp_path / 'different.json'})
            with pytest.raises(ServiceError, match='service_config_mismatch'):
                await ServiceClient.connect(different, transport=transport)
            client = await ServiceClient.connect(options, transport=transport)
            try:
                for path in ('http://example.invalid/private', '//example.invalid', '/api/v1/../other'):
                    with pytest.raises(ServiceError, match='invalid_service_path'):
                        await client.request('GET', path)
            finally:
                await client.aclose()
    asyncio.run(scenario())


def test_client_rejects_handshake_instance_mismatch_without_leaking_token(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (_, _, app, options):
            def response(request):
                return httpx.Response(200, json={'identity': options.identity.model_dump(mode='json'), 'instance_id': '0' * 32})
            with pytest.raises(ServiceError, match='service_instance_mismatch') as caught:
                await ServiceClient.connect(options, transport=httpx.MockTransport(response))
            assert app.state.owner.record.access_token not in str(caught.value)
    asyncio.run(scenario())
