"""Usage identity follows framework model calls, not Agent logical turns."""

import asyncio
import json
import logging

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_prompt_cache_call_paths import cached_application, completion_response


@async_test
async def test_native_model_identity_callbacks_and_thinking_binding_are_wired(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.model_access import MODEL_ID_METADATA_KEY
    caplog.set_level(logging.INFO)
    sent, events = [], []
    def serve(request):
        body = json.loads(request.content)
        sent.append(body)
        return completion_response(body, text='done')
    async with cached_application(monkeypatch, tmp_path, serve) as (app, catalog):
        app.host.observability.subscribe(events.append)
        sid = await app.host.sessions.create('root')
        await app.host.sessions.select_thinking(sid, 'root', 'low')
        assert (await app.host.runs.start(sid, [('user', 'PRIVATE_TASK')]))['messages'][-1].content == 'done'
        assert catalog.get('root').metadata[MODEL_ID_METADATA_KEY] == 'root'
        assert catalog.get('root').reasoning_effort is None
    starts = [e for e in events if e.event_type == 'model_call_started']
    ends = [e for e in events if e.event_type == 'model_call_finished']
    assert len(starts) == len(ends) == len(sent) == 1
    assert starts[0].data.model_id == 'root' and starts[0].data.purpose == 'agent'
    assert starts[0].data.invocation_id == ends[0].data.invocation_id
    assert ends[0].data.input_tokens == 100 and ends[0].data.usage_status == 'final'
    assert sent[0]['reasoning_effort'] == 'low' and 'naiyue_model_id' not in sent[0]
    logs = [r.application_event for r in caplog.records if hasattr(r, 'application_event')]
    assert next(r for r in logs if r['event'] == 'model_call_finished')['invocation_id'] == ends[0].data.invocation_id
    assert 'PRIVATE_TASK' not in repr(logs)


@async_test
async def test_overflow_is_three_invocations_but_one_logical_call(monkeypatch):
    from test_context_operation_events import _configured_model, _status, _app, _history
    model = _configured_model([_status(), AIMessage(content='done')])
    app = await _app(monkeypatch, {'A': model})
    events = []
    app.host.observability.subscribe(events.append)
    await app.host.runs.start(await app.host.sessions.create('A'), _history())
    starts = [e for e in events if e.event_type == 'model_call_started']
    ends = [e for e in events if e.event_type == 'model_call_finished']
    assert len(starts) == len(ends) == 3
    assert len({e.data.invocation_id for e in starts}) == 3
    assert [e.data.purpose for e in starts] == ['agent', 'summary', 'agent']
    assert [e.data.status for e in ends] == ['failed', 'returned', 'returned']
    assert {e.data.invocation_id for e in starts} == {e.data.invocation_id for e in ends}
    assert sum(e.event_type == 'turn_started' for e in events) == 1
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1


@async_test
async def test_concurrent_sessions_and_failure_do_not_share_invocation_or_scope(monkeypatch, tmp_path):
    import httpx
    entered, release, events, sent = asyncio.Event(), asyncio.Event(), [], []
    async def serve(request):
        body = json.loads(request.content)
        text = next(m['content'] for m in body['messages'] if m['role'] == 'user')
        sent.append(text)
        if text == 'hold':
            entered.set()
            await release.wait()
        if text == 'fail':
            return httpx.Response(400, json={'error': {'message': 'PRIVATE_ENDPOINT', 'type': 'invalid_request_error'}})
        return completion_response(body, text='done')
    async with cached_application(monkeypatch, tmp_path, serve) as (app, _):
        app.host.observability.subscribe(events.append)
        a, b, c = [await app.host.sessions.create('root') for _ in range(3)]
        task = asyncio.create_task(app.host.runs.start(a, [('user', 'hold')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.host.runs.start(b, [('user', 'healthy')])
            import openai
            with pytest.raises(openai.BadRequestError):
                await app.host.runs.start(c, [('user', 'fail')])
            assert not task.done()
        finally:
            release.set()
            await task
    for sid, status in ((a, 'returned'), (b, 'returned'), (c, 'failed')):
        pair = [e for e in events if e.session_id == sid and e.event_type.startswith('model_call_')]
        assert len(pair) == 2 and pair[0].data.invocation_id == pair[1].data.invocation_id
        assert pair[1].data.status == status
    assert len({e.data.invocation_id for e in events if e.event_type == 'model_call_started'}) == 3
    assert sent == ['hold', 'healthy', 'fail']


@async_test
async def test_active_stop_has_cancelled_invocation_and_original_stop_effect(monkeypatch, tmp_path):
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []
    async def serve(request):
        entered.set()
        try:
            await release.wait()
        finally:
            cancelled.set()
    async with cached_application(monkeypatch, tmp_path, serve) as (app, _):
        app.host.observability.subscribe(events.append)
        sid = await app.host.sessions.create('root')
        task = asyncio.create_task(app.host.runs.start(sid, [('user', 'task')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.host.runs.stop(sid)
            result = await asyncio.wait_for(task, 5)
            assert result['run_stopped'] is True and cancelled.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    pair = [e for e in events if e.event_type.startswith('model_call_')]
    assert len(pair) == 2 and pair[0].data.invocation_id == pair[1].data.invocation_id
    assert pair[-1].data.status == 'cancelled' and pair[-1].data.usage_status == 'unavailable'
    assert events[-1].event_type == 'run_finished'


@async_test
async def test_auto_stages_remain_distinct_when_they_share_the_agent_model(monkeypatch, tmp_path):
    from naiyue_test_agent.settings import AppSettings
    from test_auto_permission_review import settings as review_settings, verdict
    from test_dynamic_tools import definition
    sent, events, effects = [], [], []
    def serve(request):
        body = json.loads(request.content)
        sent.append(body)
        prompt = body['messages'][0]['content']
        if prompt.startswith('You are a tool permission reviewer'):
            text = verdict('ask' if 'Quickly screen' in prompt else 'allow').content
            return completion_response(body, text=text)
        if any(m['role'] == 'tool' for m in body['messages']):
            return completion_response(body, text='done')
        return completion_response(body, call=('probe', {'value': 'approved'}, 'probe'))
    options = AppSettings(permissions=review_settings(fast_model_id='root', detailed_model_id='root').permissions)
    async with cached_application(monkeypatch, tmp_path, serve, settings=options,
            tools=[definition(calls=effects, approved=True)]) as (app, _):
        app.host.observability.subscribe(events.append)
        result = await app.host.runs.start(await app.host.sessions.create('root'), [('user', 'task')])
        assert result['messages'][-1].content == 'done'
    starts = [e for e in events if e.event_type == 'model_call_started']
    ends = [e for e in events if e.event_type == 'model_call_finished']
    assert len(starts) == len(ends) == len(sent) == 4
    assert [e.data.purpose for e in starts] == ['agent', 'auto_fast', 'auto_detailed', 'agent']
    assert {e.data.model_id for e in starts} == {'root'}
    assert {e.data.invocation_id for e in starts} == {e.data.invocation_id for e in ends}
    assert effects == [('probe', 'approved')]
