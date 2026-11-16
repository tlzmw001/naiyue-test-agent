"""Saved message samples survive SQLite reopen and power read-only previews."""
import asyncio
from langchain_core.messages import AIMessage
from naiyue_test_agent.agent_runtime.context_calibration import KEY
from test_context_calibration_runtime import identified
from test_context_inspector import model_with_limits
from test_web_api import web_client, request


def test_sqlite_reopen_keeps_calibrated_preview_and_frozen_snapshot(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        model = identified(model_with_limits([AIMessage(content='answer', usage_metadata={
            'input_tokens': 2000, 'output_tokens': 5, 'total_tokens': 2005})]))
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}/context'
            run = (await client.post(f'/api/v1/scopes/chats/sessions/{sid}/runs', json=request('question'))).json()['run_id']
            await web.state.delivery.wait(run)
            value = (await client.get(endpoint)).json()
            assert value['latest_request']['version'] == 2
            frozen = value['latest_request']['estimate']['calibration']['calibrated_input_tokens']
            assert frozen == value['latest_request']['estimate']['estimated_input_tokens']
            assert value['latest_request']['usage']['input_tokens'] == 2000
            estimate = value['current']['estimate']
            assert estimate['calibration']['method'] == 'ratio_estimate'
            raw = value['latest_request']['estimate']['estimated_input_tokens']
            assert estimate['calibration']['calibrated_input_tokens'] == (estimate['estimated_input_tokens'] * 2000 + raw - 1) // raw
            assert len(model.seen) == 1
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            reopened = (await client.get(endpoint)).json()
            assert reopened == value
            assert len(model.seen) == 1
    asyncio.run(scenario())


def test_preview_uses_thinking_and_rejects_changed_configuration(monkeypatch, tmp_path):
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.application.sessions import session_context
    async def scenario():
        model = identified(model_with_limits([]))
        selected = []
        original = ModelCatalog.with_thinking
        def resolve(catalog, model_id, level):
            selected.append((model_id, level))
            return original(catalog, model_id, level)
        monkeypatch.setattr(ModelCatalog, 'with_thinking', resolve)
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = (await client.post('/api/v1/scopes/chats/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/v1/scopes/chats/sessions/{sid}/context'
            selected.clear()
            assert (await client.get(endpoint)).json()['current']['basis'] == 'next_request'
            assert selected == [('A', 'model_default')]
            session = web.state.service.application.sessions._sessions.require(sid)
            original_prepare = session_context.prepare_resources
            async def change_during_preview(*args, **kwargs):
                resources = await original_prepare(*args, **kwargs)
                with session._runtime.lock:
                    session._runtime.state.thinking_level = 'high'
                return resources
            monkeypatch.setattr(session_context, 'prepare_resources', change_during_preview)
            value = (await client.get(endpoint)).json()
            assert value['current'] == {'basis': 'unavailable', 'estimate': None}
            assert model.seen == []
    asyncio.run(scenario())


def test_regeneration_uses_only_retained_prefix_samples(monkeypatch, tmp_path):
    from uuid import uuid4
    from test_persistent_sessions import build
    async def scenario():
        model = identified(model_with_limits([
            AIMessage(content='first', usage_metadata={'input_tokens': 100, 'output_tokens': 1, 'total_tokens': 101}),
            AIMessage(content='replaced', usage_metadata={'input_tokens': 900, 'output_tokens': 1, 'total_tokens': 901}),
            AIMessage(content='replacement')]))
        async with build(monkeypatch, tmp_path / 'sessions.sqlite', model) as app:
            sid = await app.host.sessions.create('A')
            first = await app.host.runs.start(sid, [('user', 'one')])
            first_record = first['messages'][-1].response_metadata[KEY]
            second = await app.host.runs.start(sid, [('user', 'two')])
            discarded = second['messages'][-1].response_metadata[KEY]
            boundary = app.host.sessions._persistence.storage.replies.latest(sid)
            result = await app.host.runs.regenerate(sid, request_id=str(uuid4()),
                expected_reply_id=boundary['reply_id'], expected_conversation_revision=0)
            records = [m.response_metadata[KEY] for m in result['messages'] if KEY in m.response_metadata]
            assert records == [first_record]
            assert discarded not in records
            estimate = result['messages'][-1].response_metadata['naiyue_context_snapshot']['estimate']
            sample = first_record['agent']['text']
            assert estimate['calibration']['calibrated_input_tokens'] == (
                estimate['estimated_input_tokens'] * sample['actual_input_tokens']
                + sample['raw_input_tokens'] - 1) // sample['raw_input_tokens']
            assert len(model.seen) == 3
    asyncio.run(scenario())


def test_nonempty_response_format_reaches_history_preview(monkeypatch, tmp_path):
    from scripts.component_fixtures import open_component_application
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    async def scenario():
        model = identified(model_with_limits([AIMessage(content='saved', usage_metadata={
            'input_tokens': 2000, 'output_tokens': 1, 'total_tokens': 2001})]))
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        options = dict(database_path=tmp_path / 'sessions.sqlite', tools=[], system_prompt='')
        async with open_component_application('unused', response_format=None, **options) as app:
            sid = await app.host.sessions.create('A')
            await app.host.runs.start(sid, [('user', 'saved question')])
            assert (await app.host.history.context(sid))['current']['estimate']['calibration']['method'] == 'ratio_estimate'
        response_format = {'type': 'object', 'properties': {'answer': {'type': 'string'}}}
        async with open_component_application('unused', response_format=response_format, **options) as app:
            value = await app.host.history.context(sid)
            estimate = value['current']['estimate']
            assert estimate is not None
            assert estimate['calibration']['method'] == 'uncalibrated'
            assert estimate['calibration']['invalidation_reason'] == 'unsupported_request_shape'
            assert estimate['calibration']['calibrated_input_tokens'] == estimate['estimated_input_tokens']
            assert len(model.seen) == 1
    asyncio.run(scenario())
