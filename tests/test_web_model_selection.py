"""A public model ID selects the configured backend object without leaking SDK state."""

import asyncio

import httpx
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.application.sessions.project_instructions import BASE_SYSTEM_PROMPT
from naiyue_test_agent.web.app import create_web_app
from naiyue_test_agent.web.settings import WebSettings
from test_async_application import StreamingToolModel
from test_web_api import HEADERS, ORIGIN, request


def test_web_switches_configured_models_and_preserves_history(monkeypatch, tmp_path):
    async def scenario():
        first = StreamingToolModel(replies=[AIMessage(content='answer A')])
        second = StreamingToolModel(replies=[AIMessage(content='answer B')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': first, 'B': second}))
        assets = tmp_path / 'assets'
        assets.mkdir()
        (assets / 'index.html').write_text('<html>fixture</html>')
        settings = WebSettings(config_path='unused', database_path=tmp_path / 'sessions.sqlite',
            frontend_dist=assets, port=8123, access_token='unit-test-access')
        app = create_web_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=HEADERS) as client:
                assert (await client.get('/api/models')).json()['model_ids'] == ['A', 'B']
                sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
                for text, expected in [('first', 'answer A'), ('next', 'answer B')]:
                    run = (await client.post(f'/api/sessions/{sid}/runs', json=request(text))).json()['run_id']
                    await client.get(f'/api/runs/{run}/events')
                    assert (await client.get(f'/api/runs/{run}')).json()['messages'][-1]['content'] == expected
                    assert (await client.patch(f'/api/sessions/{sid}/model', json={'model_id': 'B'})).status_code == 200
                assert len(first.seen) == len(second.seen) == 1
                assert [m.content for m in second.seen[0]] == [BASE_SYSTEM_PROMPT, 'first', 'answer A', 'next']
                assert (await client.patch(f'/api/sessions/{sid}/model', json={'model_id': 'absent'})).status_code == 422
                assert (await client.get(f'/api/sessions/{sid}')).json()['model_id'] == 'B'
    asyncio.run(scenario())
