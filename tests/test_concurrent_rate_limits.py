"""Offline classified rate limits preserve each call site's failure contract."""

import asyncio

import httpx
import openai
import pytest
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai.chat_models.base import OpenAIRateLimitError

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_auto_permission_review import model as review_model, settings as auto_settings, verdict
from test_cross_session_concurrency import SessionModel, concurrent_app, drain, gated
from test_dynamic_tools import definition
from test_subagent_execution import role


def rate_limit(kind):
    if kind == 'framework':
        return ModelRateLimitError('PRIVATE_PROVIDER_BODY')
    response = httpx.Response(429, request=httpx.Request('POST', 'https://provider.invalid/v1'),
                              headers={'x-request-id': 'PRIVATE_HEADER'})
    # Real ChatOpenAI translates the SDK exception into this SDK/framework
    # subclass before it crosses our model boundary (base.py:_handle_openai_api_error).
    return OpenAIRateLimitError('PRIVATE_PROVIDER_BODY', response=response,
                                body={'error': {'code': 'rate_limit_exceeded', 'message': 'PRIVATE_BODY'}})


@pytest.mark.parametrize('kind', ['sdk', 'framework'])
@pytest.mark.parametrize('child', [False, True])
@async_test
async def test_rate_limit_root_or_child_has_no_cross_session_cascade(monkeypatch, tmp_path, caplog, kind, child):
    failure = rate_limit(kind)
    model = SessionModel(replies=[])
    children = StreamingToolModel(replies=[failure]) if child else None
    specs = [create_subagent_tool(), definition()] if child else []
    options = AppSettings(max_active_runs=2, subagents=(role(),) if child else ())
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite', model=model,
                              settings=options, tools=specs, child=children) as (app, _):
        a, b, c = [await app.acreate_session('A') for _ in range(3)]
        events, tasks = [], []
        app.subscribe_events(events.append)
        model.responses[a] = ([call('subagent', {'agent': 'tester', 'task': 'check'}, 'child'),
                               AIMessage(content='parent handles failed child')] if child else [failure])
        entered, _ = gated(model, b)
        tasks.append(asyncio.create_task(app.arun(b, [('user', b)])))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if child:
                result = await app.arun(a, [('user', a)])
                feedback, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
                assert feedback.status == 'error' and 'Subagent model request failed' in feedback.content
                assert 'limit_exceeded' in feedback.content and 'PRIVATE' not in feedback.content
                assert result['messages'][-1].content == 'parent handles failed child'
                assert len(model.requests[a]) == 2 and len(children.seen) == 1
                record, = await app.aget_chat_subagents(a)
                assert record['status'] == 'failed'
            else:
                with pytest.raises(type(failure)) as caught:
                    await app.arun(a, [('user', a)])
                assert caught.value is failure
                assert len(model.requests[a]) == 1
                assert not any(isinstance(m, ToolMessage) for m in await app.aget_messages(a))
            relevant = [e for e in events if e.session_id == a and e.event_type == 'run_finished']
            failed, = [e for e in relevant if (e.source is not None) == child]
            assert failed.data.status == 'failed'
            assert failed.data.failure.stage == 'execute' and failed.data.failure.category == 'limit_exceeded'
            assert failed.data.failure.reason == 'limit_unspecified'
            assert failed.data.failure.error_type == ('ToolException' if child else type(failure).__name__)
            assert not tasks[0].done() and set(app._persistence._active) == {b}
            assert app._storage.get(b)['state'] == 'running'
            assert (await app.arun(c, [('user', c)]))['messages'][-1].content == 'done:' + c
            model.gates[b][1].set()
            assert (await tasks[0])['messages'][-1].content == 'done:' + b
            assert app._storage.get(b)['state'] == app._storage.get(c)['state'] == 'ready'
            assert app._persistence._active == {} and app._operations == {}
        finally:
            await drain(tasks, model)
    assert 'PRIVATE' not in caplog.text
    assert not any(word in caplog.text for word in ('observation_failed', 'run_event_publish_failed'))


@pytest.mark.parametrize('kind', ['sdk', 'framework'])
@pytest.mark.parametrize('stage', ['fast', 'detail'])
@async_test
async def test_auto_rate_limit_requires_human_while_other_root_runs(monkeypatch, tmp_path, caplog, kind, stage):
    primary, effects = SessionModel(replies=[]), []
    failing = StreamingToolModel(replies=[rate_limit(kind)], metadata=review_model([]).metadata)
    fast = failing if stage == 'fast' else StreamingToolModel(replies=[verdict('ask')], metadata=failing.metadata)
    detail = StreamingToolModel(replies=[], metadata=failing.metadata) if stage == 'fast' else failing
    catalog = ModelCatalog('main', {'main': primary, 'fast': fast, 'detail': detail})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: catalog)
    options = auto_settings().model_copy(update={'max_active_runs': 2})
    async with open_async_persistent_application('unused', database_path=tmp_path / 'state.sqlite',
        tools=[definition(calls=effects, approved=True)], settings=options,
        system_prompt='', response_format=None) as app:
        a, b = [await app.acreate_session('main') for _ in range(2)]
        primary.responses[a] = [call('probe', {'value': 'approved'}, 'needs-human'), AIMessage(content='approved')]
        entered, _ = gated(primary, b)
        task = asyncio.create_task(app.arun(b, [('user', b)]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            paused = await app.arun(a, [('user', a)])
            assert [r['tool_call_id'] for r in paused['approval_requests']] == ['needs-human']
            assert effects == [] and len(primary.requests[a]) == 1 and len(failing.seen) == 1
            assert len(fast.seen) == 1 and len(detail.seen) == (stage == 'detail')
            assert not task.done() and set(app._persistence._active) == {b}
            assert (await app.aresume_tool_approval(a, ['approve']))['messages'][-1].content == 'approved'
            assert effects == [('probe', 'approved')] and len(failing.seen) == 1
            primary.gates[b][1].set()
            assert (await task)['messages'][-1].content == 'done:' + b
            assert app._storage.get(a)['state'] == app._storage.get(b)['state'] == 'ready'
        finally:
            await drain([task], primary)
    assert 'PRIVATE' not in caplog.text
    assert not any(word in caplog.text for word in ('observation_failed', 'run_event_publish_failed'))


@async_test
async def test_stop_during_model_wait_then_other_root_rate_limit_is_isolated(monkeypatch, tmp_path, caplog):
    async with concurrent_app(monkeypatch, tmp_path / 'state.sqlite', settings=AppSettings(max_active_runs=2)) as (app, model):
        a, b, c = [await app.acreate_session('A') for _ in range(3)]
        model.responses[b] = [rate_limit('sdk')]
        tasks = []
        try:
            for sid in (a, b):
                entered, _ = gated(model, sid)
                tasks.append(asyncio.create_task(app.arun(sid, [('user', sid)])))
                await asyncio.wait_for(entered.wait(), 5)
            assert app.request_stop(a) == []
            assert (await tasks[0])['run_stopped'] and not tasks[1].done()
            assert set(app._persistence._active) == {b}
            assert (await app.arun(c, [('user', c)]))['messages'][-1].content == 'done:' + c
            model.gates[b][1].set()
            with pytest.raises(openai.RateLimitError):
                await tasks[1]
            assert app._persistence._active == {} and app._operations == {}
            assert len(model.requests[a]) == len(model.requests[b]) == len(model.requests[c]) == 1
        finally:
            await drain(tasks, model)
    assert 'PRIVATE' not in caplog.text
