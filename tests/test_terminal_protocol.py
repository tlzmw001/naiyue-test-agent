"""Terminal-only projections and output confirmation remain service owned."""
import asyncio
from uuid import uuid4
from langchain_core.messages import AIMessage
from test_async_application import StreamingToolModel
from test_service_protocol import service_clients, command


def test_terminal_history_chunks_preserve_complete_text_and_recovery(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='reply' * 2000)])) as (a, _, app, _):
            base = '/api/v1/scopes/chats'
            sid = (await a.post(base + '/sessions', json={'model_id': 'A'}, headers=command())).json()['session_id']
            key = str(uuid4())
            rid = (await a.post(f'{base}/sessions/{sid}/runs', json={'request_id': key, 'text': 'question'}, headers={'X-Request-ID': key})).json()['run_id']
            await app.state.scopes.services['chats'].delivery.wait(rid)
            response = await a.get(f'{base}/terminal/sessions/{sid}/history')
            assert response.status_code == 200
            body = response.json()
            assert 'recovery' in body and 'messages' not in body
            row = await a.get(f'{base}/terminal/sessions/{sid}/message?index=1&offset=0')
            assert row.status_code == 200
            import json
            assert json.loads(row.json()['text'])['content'] == 'reply' * 2000
    asyncio.run(scenario())


def test_output_confirmation_is_scope_bound_one_use_and_idempotent(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from test_multi_project_runtime import projects
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (a, _, app, _):
            (pa, _), _ = await projects(app.state.application, tmp_path)
            service = await app.state.scopes.get('chats')
            plan = SimpleNamespace(file_count=2, total_bytes=42)
            preview = AsyncMock(return_value=plan)
            clear = AsyncMock(return_value=(2, 42))
            monkeypatch.setattr(service.application.tasks, 'preview_outputs', preview)
            monkeypatch.setattr(service.application.tasks, 'clear_outputs', clear)
            base = '/api/v1/scopes/chats/terminal/outputs'
            response = await a.post(base + '/preview', headers=command())
            assert response.status_code == 200
            confirmation = response.json()['confirmation_id']
            body = {'confirmation_id': confirmation}
            foreign = await a.post(f'/api/v1/scopes/{pa.project_id}/terminal/outputs/clear', json=body, headers=command())
            assert foreign.json()['error']['code'] == 'output_confirmation_invalid'
            clear.assert_not_awaited()
            key = command()
            first = await a.post(base + '/clear', json=body, headers=key)
            replay = await a.post(base + '/clear', json=body, headers=key)
            assert first.status_code == replay.status_code == 200
            assert first.json() == replay.json() == {'file_count': 2, 'total_bytes': 42}
            clear.assert_awaited_once_with(plan)
            reused = await a.post(base + '/clear', json=body, headers=command())
            assert reused.json()['error']['code'] == 'output_confirmation_invalid'
    asyncio.run(scenario())
