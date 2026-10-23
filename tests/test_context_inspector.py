"""Read-only composition uses real model inputs, not presentation history."""

from runtime_context_helpers import RunIdentity, ModelCallState, typed_context

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent.model_access import MODEL_LIMITS_METADATA_KEY, COMPACTION_RESERVE_METADATA_KEY
from test_async_application import StreamingToolModel
from test_web_api import web_client, request


def model_with_limits(replies):
    return StreamingToolModel(replies=replies, metadata={
        MODEL_LIMITS_METADATA_KEY: {'model_id': 'A', 'context_window_tokens': 128000,
            'max_output_tokens': 16384, 'context_window_source': 'config', 'max_output_source': 'default'},
        COMPACTION_RESERVE_METADATA_KEY: {'tokens': 24000, 'source': 'config'},
    })


def test_estimate_is_additive_and_has_no_content():
    from naiyue_test_agent.agent_runtime.context_usage import estimate_context
    messages = [HumanMessage(content='SECRET_USER'), AIMessage(content='SECRET_AI'),
        ToolMessage(content='SECRET_TOOL', tool_call_id='call'),
        HumanMessage(content='SECRET_SUMMARY', additional_kwargs={'naiyue_compaction_summary': True})]
    system = SystemMessage(content='SECRET_SYSTEM')
    estimate = estimate_context(model_with_limits([]), system, messages, [])
    assert estimate['estimated_input_tokens'] == count_tokens_approximately([system, *messages])
    assert sum(row['tokens'] for row in estimate['groups']) == estimate['estimated_input_tokens']
    assert estimate['trigger_threshold_tokens'] == 104000
    assert estimate['context_window_source'] == 'config'
    assert 'SECRET' not in json.dumps(estimate)
    assert {row['id'] for row in estimate['groups']} >= {'system', 'user', 'assistant', 'tool_results', 'summary'}


def test_read_only_preview_and_report_survive_checkpoint_reopen(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        (root / 'AGENTS.md').write_text('SECRET_PROJECT')
        reply = AIMessage(content='SECRET_REPLY', usage_metadata={
            'input_tokens': 100, 'output_tokens': 8, 'total_tokens': 108,
            'input_token_details': {'cache_read': 0, 'cache_creation': 20}})
        model = model_with_limits([reply])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            endpoint = f'/api/sessions/{sid}/context'
            first = await client.get(endpoint)
            assert first.status_code == 200
            assert first.json()['current']['basis'] == 'next_request'
            assert first.json()['latest_request'] is None
            assert model.seen == []
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('SECRET_USER'))).json()['run_id']
            await client.get(f'/api/runs/{run}/events')
            value = (await client.get(endpoint)).json()
            saved = value['latest_request']
            assert saved['model_call_id']
            assert saved['usage'] == {'input_tokens': 100, 'output_tokens': 8, 'cache_read_tokens': 0, 'cache_write_tokens': 20}
            assert saved['estimate']['estimated_input_tokens'] < value['current']['estimate']['estimated_input_tokens']
            system = next(row for row in saved['estimate']['groups'] if row['id'] == 'system')
            assert any(row['id'] == 'project' and row['source'] == 'AGENTS.md' for row in system['children'])
            assert 'SECRET' not in json.dumps(value)
            assert len(model.seen) == 1
            assert (await client.get(endpoint, headers={'Authorization': ''})).status_code == 401
            assert (await client.get('/api/sessions/missing/context')).status_code == 404
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, app):
            reopened = (await client.get(endpoint)).json()
            assert reopened['latest_request'] == saved
            assert len(model.seen) == 1
    asyncio.run(scenario())


def test_live_snapshot_is_frozen_and_usage_is_not_guessed(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        (root / 'AGENTS.md').write_text('FIRST_CONTENT')
        entered, release = asyncio.Event(), asyncio.Event()
        model = model_with_limits([AIMessage(content='done')])
        model.entered, model.release = entered, release
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('task'))).json()['run_id']
            try:
                await asyncio.wait_for(entered.wait(), 5)
                first = (await client.get(f'/api/sessions/{sid}/context')).json()
                assert first['current']['basis'] == 'last_sent'
                assert first['latest_request']['status'] == 'running'
                assert first['latest_request']['usage'] is None
                (root / 'AGENTS.md').write_text('CHANGED_CONTENT' * 100)
                assert (await client.get(f'/api/sessions/{sid}/context')).json() == first
                assert len(model.seen) == 1
            finally:
                release.set()
            await web.state.delivery.wait(run)
            after = (await client.get(f'/api/sessions/{sid}/context')).json()
            assert after['current']['basis'] == 'next_request'
            assert after['latest_request']['usage'] is None
            assert after['current']['estimate']['estimated_input_tokens'] > first['current']['estimate']['estimated_input_tokens']
            assert after['latest_request']['model_call_id'] == first['latest_request']['model_call_id']
    asyncio.run(scenario())


def test_preview_fault_does_not_change_checkpoint_or_start_model(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.application.sessions import session_context
    async def scenario():
        model = model_with_limits([])
        async with web_client(monkeypatch, tmp_path, model) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            before = (await client.get(f'/api/sessions/{sid}')).json()
            def broken(*args, **kwargs):
                raise OSError('SECRET_RESOURCE_PATH')
            monkeypatch.setattr(session_context, 'prepare_run_resources', broken)
            value = (await client.get(f'/api/sessions/{sid}/context')).json()
            assert value['current'] == {'basis': 'unavailable', 'estimate': None}
            assert (await client.get(f'/api/sessions/{sid}')).json() == before
            assert model.seen == []
    asyncio.run(scenario())
    assert 'context_preview_unavailable' in caplog.text
    assert 'SECRET_RESOURCE_PATH' not in caplog.text


def test_child_usage_does_not_replace_root_snapshot(monkeypatch, tmp_path):
    from test_subagent_execution import ChildModel, role
    from test_async_application import call
    from naiyue_test_agent.settings import AppSettings
    async def scenario():
        model = ChildModel(replies=[call('subagent', {'agent': 'tester', 'task': 'child task'}, 'outer'),
            AIMessage(content='child', usage_metadata={'input_tokens': 900, 'output_tokens': 3, 'total_tokens': 903}),
            AIMessage(content='root', usage_metadata={'input_tokens': 120, 'output_tokens': 2, 'total_tokens': 122})])
        root = tmp_path / 'workspace'
        root.mkdir()
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root,
                runtime_settings=AppSettings(subagents=(role(tool_names=(), model_id=None),))) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('parent task'))).json()['run_id']
            await web.state.delivery.wait(run)
            view = (await client.get(f'/api/sessions/{sid}/context')).json()
            assert view['latest_request']['usage']['input_tokens'] == 120
            snapshots = [event for event in web.state.delivery.get(run).events if event['event_type'] == 'model_context_snapshot']
            assert any(event['source'] for event in snapshots)
            root = [json.loads(event['data']['snapshot_json']) for event in snapshots if not event['source']]
            assert root[-1] == view['latest_request']
            assert len(model.seen) == 3
    asyncio.run(scenario())


def test_observation_fault_preserves_original_model_failure(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.agent_runtime import context_observation
    from test_async_application import make_app
    async def scenario():
        failure = RuntimeError('SECRET_PROVIDER_BODY')
        model = model_with_limits([failure])
        def broken(*args, **kwargs):
            raise ValueError('SECRET_ESTIMATION')
        monkeypatch.setattr(context_observation, 'estimate_context', broken)
        app = await make_app(monkeypatch, model)
        try:
            sid = await app.sessions.create('A')
            try:
                await app.runs.start(sid, [('user', 'task')])
            except RuntimeError as caught:
                assert caught is failure
            else:
                raise AssertionError('Original failure must propagate')
        finally:
            await app.aclose()
    asyncio.run(scenario())
    assert 'context_snapshot_unavailable' in caplog.text
    assert 'SECRET_ESTIMATION' not in caplog.text


def test_images_and_summary_are_counted_once_without_encoded_pixels():
    from naiyue_test_agent.agent_runtime.context_usage import estimate_context
    from naiyue_test_agent.model_access.messages import MODEL_INPUT_METADATA_KEY, prepare_model_messages
    model = model_with_limits([])
    model.profile = {'image_inputs': True}
    model.metadata[MODEL_INPUT_METADATA_KEY] = {'api': 'chat_completions'}
    history = [AIMessage(content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'i'}]),
        ToolMessage(content=[{'type': 'image', 'base64': 'SECRET_PIXELS', 'mime_type': 'image/png'}], tool_call_id='i')]
    messages = prepare_model_messages(history, model)
    estimate = estimate_context(model, SystemMessage(content='base'), messages, [])
    assert estimate['image_count'] == 1
    assert next(row for row in estimate['groups'] if row['id'] == 'tool_results')['count'] == 2
    assert not any(row['id'] == 'user' for row in estimate['groups'])
    assert 'SECRET_PIXELS' not in json.dumps(estimate)


def test_saved_snapshot_rejects_unexpected_content_fields(caplog):
    from naiyue_test_agent.agent_runtime.context_usage import read_snapshot, request_snapshot, finish_snapshot, estimate_context
    snapshot = finish_snapshot(request_snapshot(typed_context(identity=RunIdentity('s', 'run'), model_call=ModelCallState('call', 'A')),
        estimate_context(model_with_limits([]), SystemMessage(content='base'), [], [])))
    assert read_snapshot(snapshot) == snapshot
    snapshot['estimate']['groups'][0]['body'] = 'SECRET_BODY'
    assert read_snapshot(snapshot) is None
    assert 'SECRET_BODY' not in caplog.text


def test_pending_approval_keeps_latest_request_but_is_not_a_next_request_preview(monkeypatch, tmp_path):
    from test_async_application import call
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        outside = tmp_path / 'outside.txt'
        outside.write_text('NOT_READ_BEFORE_APPROVAL')
        model = model_with_limits([call('read', {'path': str(outside)}, 'read-outside')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('read outside'))).json()['run_id']
            await web.state.delivery.wait(run)
            session = (await client.get(f'/api/sessions/{sid}')).json()
            assert session['pending_approval'] and not session['busy']
            value = (await client.get(f'/api/sessions/{sid}/context')).json()
            assert value['current'] == {'basis': 'unavailable', 'estimate': None}
            assert value['latest_request']['status'] == 'returned'
            assert not any(row['type'] == 'tool' for row in session['messages'])
            assert len(model.seen) == 1
    asyncio.run(scenario())


def test_optional_context_statistics_do_not_overflow_main_delivery(monkeypatch, tmp_path):
    async def scenario():
        root = tmp_path / 'workspace'
        root.mkdir()
        (root / 'AGENTS.md').write_text('RULE')
        model = model_with_limits([AIMessage(content='ok')])
        async with web_client(monkeypatch, tmp_path, model, workspace_root=root) as (client, web):
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('short'))).json()['run_id']
            await web.state.delivery.wait(run)
            from naiyue_test_agent.web.serialization import encoded_size
            baseline = (await client.get(f'/api/sessions/{sid}')).json()
            assert baseline['live']['context_snapshot'] is not None
            baseline['live']['context_snapshot'] = None
            # Derive a valid core-message budget, independent of temp path length/OS.
            settings = web.state.service.settings.model_copy(update={'max_frame_bytes': encoded_size(baseline) + 1})
            web.state.service.settings = settings
            web.state.delivery.get(run).settings = settings
            view = (await client.get(f'/api/runs/{run}')).json()
            assert view['status'] == 'completed' and not view['incomplete']
            assert view['messages'][-1]['content'] == 'ok'
            stream = await client.get(f'/api/runs/{run}/events')
            assert stream.status_code == 200
            session = await client.get(f'/api/sessions/{sid}')
            assert session.status_code == 200 and session.json()['live']['context_snapshot'] is None
            assert len(model.seen) == 1
    asyncio.run(scenario())


def test_oversized_stat_event_keeps_its_sequence_with_an_omitted_payload():
    from naiyue_test_agent.web.settings import WebSettings
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData, RunFinishedData
    from naiyue_test_agent.agent_runtime.context_usage import estimate_context, request_snapshot, ModelContextSnapshotData
    from naiyue_test_agent.web.delivered_run import DeliveredRun
    async def scenario():
        job = DeliveredRun('s', [], WebSettings(database_path='unused', frontend_dist='unused',
            max_frame_bytes=800, event_capacity=20), lambda _: None)
        publisher = RunEventPublisher('s', 'r', (job.observe,))
        publisher.publish('run_started', RunStartedData('message'))
        snapshot = request_snapshot(typed_context(identity=RunIdentity('s', 'r'), model_call=ModelCallState('call', 'A')),
            estimate_context(model_with_limits([]), SystemMessage(content='base'), [], []))
        publisher.publish('model_context_snapshot', ModelContextSnapshotData('call', json.dumps(snapshot)))
        publisher.publish('run_finished', RunFinishedData('completed'))
        await job.started
        assert [event['sequence'] for event in job.events] == [1, 2, 3]
        assert job.events[1]['data'] == {'model_call_id': 'call', 'snapshot_json': None}
        assert job.view()['status'] == 'completed' and not job.view()['incomplete']
    asyncio.run(scenario())
