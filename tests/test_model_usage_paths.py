"""Usage reaches existing root/child, summary, Auto and Web observations."""

import json
import logging

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
from naiyue_test_agent.tools import create_subagent_tool
from naiyue_test_agent.settings import AppSettings
from test_prompt_cache_call_paths import cached_application, completion_response
from test_auto_permission_review import settings as review_settings, verdict
from test_dynamic_tools import definition
from test_subagent_execution import role


@async_test
async def test_root_child_summary_and_auto_report_independent_cache_usage(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    totals = {'root': (100, 30, 20), 'child': (200, 80, 60), 'fast': (300, 0, 90), 'detail': (400, 100, 120)}
    sent, events, effects = [], [], []
    def serve(request):
        body = json.loads(request.content)
        sent.append(body)
        name, messages = body['model'], body['messages']
        if name in ('fast', 'detail'):
            text, call = verdict('ask' if name == 'fast' else 'allow').content, None
        elif messages[0]['content'].startswith('Summarize completed conversation history'):
            text, call = 'Earlier task delegated successfully.', None
        elif name == 'child':
            text, call = 'child report', None
        elif len([m for m in messages if m['role'] == 'user']) > 1:
            text, call = 'recent answer', None
        else:
            results = [m for m in messages if m['role'] == 'tool']
            text = 'older details ' * 500 if len(results) > 1 else ''
            call = (('probe', {'value': 'parent'}, 'probe') if not results else
                    ('subagent', {'agent': 'tester', 'task': 'CHECK'}, 'delegate') if len(results) == 1 else None)
        total, read, write = totals[name]
        return completion_response(body, text=text, call=call, usage={'prompt_tokens': total,
            'completion_tokens': 10, 'total_tokens': total + 10,
            'prompt_tokens_details': {'cached_tokens': read, 'cache_write_tokens': write}})
    settings = AppSettings(permissions=review_settings().permissions, subagents=(role(model_id='child'),))
    async with cached_application(monkeypatch, tmp_path, serve, settings=settings,
            tools=[definition(calls=effects, approved=True), create_subagent_tool()]) as (app, _):
        from naiyue_test_agent.model_access.http_observation import install_http_hooks
        for identity in app._catalog.model_ids:
            install_http_hooks(app._catalog.get(identity).root_async_client._client, asynchronous=True)
        app.subscribe_events(events.append)
        sid = await app.acreate_session('root')
        await app.arun(sid, [('user', 'old task')])
        await app.arun(sid, [('user', 'recent task')])
        assert (await app.acompact_context(sid))['compaction_status'] == 'applied'
    ends = [e for e in events if e.event_type == 'model_call_finished']
    assert len(ends) == len(sent) == 8
    attempts = [e for e in events if e.event_type == 'model_http_attempt_started']
    responses = [e for e in events if e.event_type == 'model_http_attempt_finished']
    assert len(attempts) == len(responses) == len(sent) == 8
    assert {e.data.invocation_id for e in attempts} == {e.data.invocation_id for e in ends}
    assert all(e.data.http_capture_status == 'complete' for e in ends)
    for attempt in attempts:
        owner = next(e for e in ends if e.data.invocation_id == attempt.data.invocation_id)
        assert (attempt.session_id, attempt.run_id, attempt.source) == (owner.session_id, owner.run_id, owner.source)
    starts = [e for e in events if e.event_type == 'model_call_started']
    results = [e for e in events if e.event_type == 'model_call_finished']
    assert len(starts) == len(results) == 8
    assert all(type(e.data.duration_ms) is int and e.data.duration_ms >= 0 for e in results)
    assert {e.data.invocation_id for e in starts} == {e.data.invocation_id for e in results}
    assert len({e.data.invocation_id for e in starts}) == 8
    assert sorted(e.data.purpose for e in starts) == ['agent'] * 5 + ['auto_detailed', 'auto_fast', 'summary']
    for start in starts:
        finish = next(e for e in results if e.data.invocation_id == start.data.invocation_id)
        total, read, write = totals[start.data.model_id]
        assert (finish.data.input_tokens, finish.data.cache_read_tokens, finish.data.cache_write_tokens) == (total, read, write)
        assert (start.session_id, start.run_id, start.source) == (finish.session_id, finish.run_id, finish.source)
        assert bool(start.source) == (start.data.model_id == 'child')
    assert effects == [('probe', 'parent')]
    operation = next(e.data for e in events if e.event_type == 'compaction_finished')
    assert (operation.summary_input_tokens, operation.summary_cache_read_tokens, operation.summary_cache_write_tokens) == (100, 30, 20)
    reviews = [r.permission_review for r in caplog.records if hasattr(r, 'permission_review')]
    assert [(r['stage'], r['input_tokens'], r['cache_read_tokens'], r['cache_write_tokens']) for r in reviews] == [
        ('fast', 300, 0, 90), ('detailed', 400, 100, 120)]
    app_logs = [r.application_event for r in caplog.records if hasattr(r, 'application_event')]
    assert next(r for r in app_logs if r['event'] == 'compaction_finished')['summary_cache_write_tokens'] == 20
    assert sorted(r['cache_write_tokens'] for r in app_logs if r['event'] == 'model_call_finished') == [20] * 5 + [60, 90, 120]
    terminals = [e for e in events if e.event_type in ('run_finished', 'tool_call_finished')]
    assert terminals and any(e.source for e in terminals if e.event_type == 'run_finished')
    assert all(type(e.data.duration_ms) is int and e.data.duration_ms >= 0 for e in terminals)
    assert not any(word in caplog.text for word in ('observation_failed', 'run_event_publish_failed'))


@async_test
async def test_rejected_final_response_still_reports_usage_and_unchanged_failure(monkeypatch, tmp_path):
    import httpx
    events = []
    def serve(request):
        body = json.loads(request.content)
        response = completion_response(body, call=('probe', {}, 'rejected'), usage={'prompt_tokens': 100,
            'completion_tokens': 10, 'prompt_tokens_details': {'cached_tokens': 30, 'cache_write_tokens': 20}})
        return httpx.Response(200, headers=response.headers,
            content=response.content.decode().replace('"arguments": "{}"', '"arguments": "not-json"'))
    async with cached_application(monkeypatch, tmp_path, serve) as (app, _):
        app.subscribe_events(events.append)
        with pytest.raises(ModelResponseRejected) as caught:
            await app.arun(await app.acreate_session('root'), [('user', 'task')])
        assert caught.value.reason == 'invalid_tool_calls'
    end = next(e.data for e in events if e.event_type == 'model_call_finished')
    assert end.status == 'returned'
    assert events[-1].data.failure.reason == 'invalid_tool_calls'
    assert (end.input_tokens, end.output_tokens, end.cache_read_tokens, end.cache_write_tokens) == (100, 10, 30, 20)
    invocation = next(e.data for e in events if e.event_type == 'model_call_finished')
    assert invocation.status == 'returned' and invocation.usage_status == 'final'
    assert invocation.input_tokens == 100


def test_snapshot_old_optional_field_and_new_usage_validation(caplog):
    from naiyue_test_agent.agent_runtime.context_usage import estimate_context, request_snapshot, finish_snapshot, read_snapshot
    from test_context_inspector import model_with_limits
    from types import SimpleNamespace
    before = request_snapshot({'_naiyue_model_call_id': 'call', '_naiyue_model_id': 'A'},
                              estimate_context(model_with_limits([]), None, [], []))
    response = SimpleNamespace(result=[AIMessage(content='PRIVATE', usage_metadata={
        'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105,
        'input_token_details': {'cache_read': 0, 'cache_creation': 20}})])
    snapshot = finish_snapshot(before, response)
    assert snapshot['usage'] == {'input_tokens': 100, 'output_tokens': 5, 'cache_read_tokens': 0, 'cache_write_tokens': 20}
    assert read_snapshot(snapshot) == snapshot
    del snapshot['usage']['cache_write_tokens']
    restored = read_snapshot(snapshot)
    assert restored['usage']['cache_write_tokens'] is None
    assert 'cache_write_tokens' not in snapshot['usage']
    snapshot['usage']['cache_write_tokens'] = True
    assert read_snapshot(snapshot) is None
    assert 'PRIVATE' not in caplog.text


@async_test
async def test_unknown_usage_preserves_original_network_failure(monkeypatch):
    from test_async_application import StreamingToolModel, make_app
    failure, events = TimeoutError('PRIVATE_PROVIDER_BODY'), []
    app = await make_app(monkeypatch, StreamingToolModel(replies=[failure]))
    try:
        app.subscribe_events(events.append)
        with pytest.raises(TimeoutError) as caught:
            await app.arun(await app.acreate_session('A'), [('user', 'task')])
        assert caught.value is failure
    finally:
        await app.aclose()
    end = next(e.data for e in events if e.event_type == 'model_call_finished')
    assert end.status == 'failed'
    assert (end.input_tokens, end.output_tokens, end.cache_read_tokens, end.cache_write_tokens) == (None,) * 4


@async_test
async def test_invalid_cache_statistics_do_not_reject_a_successful_response(monkeypatch, caplog):
    from test_async_application import StreamingToolModel, make_app
    reply = AIMessage(content='done', usage_metadata={'input_tokens': 100, 'output_tokens': 5,
        'total_tokens': 105, 'input_token_details': {'cache_creation': -1, 'cache_read': 0}})
    app = await make_app(monkeypatch, StreamingToolModel(replies=[reply]))
    events = []
    try:
        app.subscribe_events(events.append)
        result = await app.arun(await app.acreate_session('A'), [('user', 'task')])
        assert result['messages'][-1].content == 'done'
    finally:
        await app.aclose()
    end = next(e.data for e in events if e.event_type == 'model_call_finished')
    assert end.status == 'returned' and end.input_tokens == 100
    assert end.cache_write_tokens is None and end.cache_read_tokens == 0
    assert 'model_usage_invalid' in caplog.text and 'observation_failed' not in caplog.text


@async_test
async def test_invalid_auto_verdict_retains_reported_usage_without_granting_permission(caplog):
    from test_auto_permission_review import model, reviewer, payload
    caplog.set_level(logging.INFO)
    response = AIMessage(content='PRIVATE_INVALID_JSON', usage_metadata={'input_tokens': 100,
        'output_tokens': 5, 'total_tokens': 105, 'input_token_details': {'cache_creation': 20, 'cache_read': 30}})
    fast, detailed = model([response]), model([])
    assert (await reviewer(fast, detailed).areview(payload(), {}))['decision'] == 'ask'
    record = next(r.permission_review for r in caplog.records if hasattr(r, 'permission_review'))
    assert (record['input_tokens'], record['cache_read_tokens'], record['cache_write_tokens']) == (100, 30, 20)
    assert record['reason'] == 'review_unavailable' and detailed.seen == []
    assert 'PRIVATE_INVALID_JSON' not in caplog.text


@pytest.mark.parametrize('bad', [True, -1, 'PRIVATE', 1.5])
def test_new_event_fields_keep_existing_validation(bad):
    from naiyue_test_agent.agent_runtime.events import ModelInvocationFinishedData
    from naiyue_test_agent.agent_runtime.context_events import ContextOperationFinishedData
    with pytest.raises(ValueError):
        ModelInvocationFinishedData('call', 'returned', cache_write_tokens=bad)
    with pytest.raises(ValueError):
        ContextOperationFinishedData('operation', 'compaction', 'A', 'manual', summary_cache_write_tokens=bad)
