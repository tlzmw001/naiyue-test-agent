"""Web projection must strip image pixels before event frame checks."""

import asyncio
from dataclasses import asdict
from dataclasses import replace
import hashlib
import json

from langchain_core.messages import ToolMessage

from naiyue_test_agent.agent_runtime.events import RuntimeEvent
from naiyue_test_agent.agent_runtime.message_observation import _snapshot
from naiyue_test_agent.web.delivered_run import DeliveredRun
from naiyue_test_agent.web.settings import WebSettings


def image_event():
    message = ToolMessage(id='message', tool_call_id='image', content=[
        {'type': 'text', 'text': 'image description'},
        {'type': 'image', 'mime_type': 'image/png', 'base64': 'PRIVATE_PIXELS' * 10000}])
    return RuntimeEvent('message_completed', 'session', 'run', 1, 'synthetic', _snapshot(message, 'graph_values'))


def test_web_projection_copy_has_its_own_valid_hash_and_preserves_original():
    from naiyue_test_agent.web.serialization import project_event_for_web
    event = image_event()
    original = asdict(event)
    projected = project_event_for_web(event)
    row = json.loads(projected.data.snapshot_json)
    assert row['content_projection'] == 'text' and isinstance(row['content'], str)
    assert 'image/png' in row['content'] and 'PRIVATE_PIXELS' not in projected.data.snapshot_json
    encoded = projected.data.snapshot_json.encode()
    assert projected.data.snapshot_sha256 == hashlib.sha256(encoded).hexdigest()
    assert projected.data.snapshot_bytes == len(encoded)
    assert (projected.sequence, projected.session_id, projected.run_id) == (1, 'session', 'run')
    assert (projected.data.tool_call_id, projected.data.status) == ('image', 'success')
    assert asdict(event) == original


def test_delivery_checks_frame_size_after_image_projection(tmp_path):
    async def scenario():
        job = DeliveredRun('session', [], WebSettings(access_token='unit-test', max_frame_bytes=4096,
            database_path=tmp_path / 'db.sqlite', frontend_dist=tmp_path), lambda _: None)
        event = image_event()
        # Seed the actual run boundary; the next event must have sequence 2.
        from naiyue_test_agent.agent_runtime.events import RunStartedData
        job.observe(RuntimeEvent('run_started', 'session', 'run', 1, 'synthetic', RunStartedData('message')))
        event = replace(event, sequence=2)
        assert event.data.snapshot_bytes > 4096
        job.observe(event)
        assert job.projection.incomplete is False
        assert len(job.events) == 2 and job.last_sequence == 2
        assert job.view()['messages'][0]['tool_call_id'] == 'image'
        assert 'PRIVATE_PIXELS' not in json.dumps(list(job.events))
    asyncio.run(scenario())


def test_real_http_image_run_has_bounded_text_events_and_canonical_checkpoint(monkeypatch, tmp_path):
    from io import BytesIO
    import random
    from PIL import Image
    from langchain_core.messages import AIMessage
    from test_async_application import StreamingToolModel
    from test_web_api import web_client, request
    from naiyue_test_agent.model_access.messages import MODEL_INPUT_METADATA_KEY
    async def scenario():
        root = tmp_path / 'workspace'; root.mkdir()
        encoded = BytesIO()
        Image.frombytes('RGB', (100, 100), random.Random(7).randbytes(30000)).save(encoded, format='PNG')
        (root / 'image.png').write_bytes(encoded.getvalue())
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': 'image.png'}, 'id': 'image'}]), AIMessage(content='done')],
            profile={'image_inputs': True}, metadata={MODEL_INPUT_METADATA_KEY: {'api': 'chat_completions'}})
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root, max_frame_bytes=8192) as (client, app):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('inspect'))).json()['run_id']
            events = await client.get(f'/api/v1/scopes/chats/runs/{run}/events')
            assert 'resync_required' not in events.text
            view = (await client.get(f'/api/v1/scopes/chats/runs/{run}')).json()
            assert view['status'] == 'completed' and view['incomplete'] is False
            row = next(m for m in view['messages'] if m['type'] == 'tool')
            assert row['content_projection'] == 'text' and 'pixels omitted' in row['content']
            assert len(view['messages']) == 4
            restored = (await client.get(f'/api/v1/scopes/chats/sessions/{sid}')).json()['messages']
            assert next(m for m in restored if m['type'] == 'tool')['content_projection'] == 'text'
            canonical = await app.state.delivery.application.history.messages(sid)
            tool = next(m for m in canonical if isinstance(m, ToolMessage))
            pixels = tool.content[-1]['base64']
            assert len(pixels) > 8192 and pixels not in events.text
            assert not any(m.additional_kwargs.get('naiyue_image_carrier') for m in canonical)
    asyncio.run(scenario())
