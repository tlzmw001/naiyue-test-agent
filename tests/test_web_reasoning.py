"""Reasoning details are read-only and page budget applies to visible text."""

from uuid import uuid4
from langchain_core.messages import AIMessage
from native_application_helpers import async_test
from test_web_api import web_client
from test_write_tool import RecordingFake


@async_test
async def test_large_visible_reasoning_is_previewed_and_can_be_read_in_chunks(monkeypatch, tmp_path):
    text = '可见思考' * 2000
    model = RecordingFake(responses=[AIMessage(content=[{'type': 'reasoning', 'reasoning': text},
                                                        {'type': 'text', 'text': 'answer'}])])
    async with web_client(monkeypatch, tmp_path, model, history_message_bytes=2048, history_preview_chars=64) as (client, web):
        sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
        run = await client.post(f'/api/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'q'})
        await web.state.delivery.wait(run.json()['run_id'])
        rows = (await client.get(f'/api/sessions/{sid}/messages')).json()['messages']
        row = next(r for r in rows if r['type'] == 'ai')
        assert row['reasoning_truncated'] and text.startswith(row['reasoning'])
        response = await client.get(f'/api/sessions/{sid}/messages/{row["sequence"]}/reasoning',
                                    params={'revision': row['revision'], 'offset': 0, 'conversation_revision': 0})
        assert response.status_code == 200 and text.startswith(response.json()['text'])
        assert len(model.seen) == 1
