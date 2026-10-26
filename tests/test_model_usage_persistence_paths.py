"""Persistent application wiring saves facts before releasing root ownership."""

from langchain_core.messages import AIMessage
import pytest

from native_application_helpers import async_test
from test_persistent_sessions import build
from test_application_run_events import ToolModel
from test_model_usage_storage import rows


@async_test
async def test_application_saves_before_return_reopens_and_cleans_subscription(monkeypatch, tmp_path):
    model = ToolModel(responses=[AIMessage(content='done', usage_metadata={
        'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105})])
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path, model) as app:
        sid = await app.host.sessions.create('A')
        assert (await app.host.runs.start(sid, [('user', 'PRIVATE_TASK')]))['messages'][-1].content == 'done'
        before = rows(app.host.sessions._persistence.storage)
        assert len(before) == 1 and before[0]['input_tokens'] == 100
        assert before[0]['session_id'] == sid and before[0]['status'] == 'returned'
        assert type(before[0]['duration_ms']) is int and before[0]['duration_ms'] >= 0
        assert not app.host.sessions._sessions.require(sid).describe_session()['busy']
        sink = app.host.observability._usage
    assert sink._task.done() and app.host.sessions._sessions._events._handlers == {}
    async with build(monkeypatch, path) as reopened:
        assert rows(reopened.host.sessions._persistence.storage) == before


@async_test
@pytest.mark.parametrize('model_error', [False, True])
async def test_usage_save_failure_preserves_result_or_original_error(monkeypatch, tmp_path, model_error, caplog):
    from test_async_application import StreamingToolModel
    failure = TimeoutError('PRIVATE_MODEL_ERROR')
    model = StreamingToolModel(replies=[failure if model_error else AIMessage(content='done')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.host.sessions.create('A')
        def broken(_):
            raise OSError('PRIVATE_STORE_ERROR')
        monkeypatch.setattr(app.host.sessions._persistence.storage.usage, 'save_model_usage_event', broken)
        if model_error:
            with pytest.raises(TimeoutError) as caught:
                await app.host.runs.start(sid, [('user', 'task')])
            assert caught.value is failure
        else:
            assert (await app.host.runs.start(sid, [('user', 'task')]))['messages'][-1].content == 'done'
        row, = app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_model_usage_runs')
        assert row['capture_incomplete'] == 1 and row['reason'] == 'storage_error'
        assert sid not in app.host.sessions._persistence._blocked
    assert 'model_usage_save_failed' in caplog.text and 'PRIVATE_STORE_ERROR' not in caplog.text


@async_test
async def test_native_root_child_summary_and_same_model_auto_are_saved_once(monkeypatch, tmp_path):
    import json
    from scripts.component_fixtures import open_component_application
    from naiyue_test_agent.tools import create_subagent_tool
    from naiyue_test_agent.settings import AppSettings
    from test_prompt_cache_call_paths import cached_application, completion_response
    from test_auto_permission_review import settings as review_settings, verdict
    from test_dynamic_tools import definition
    from test_subagent_execution import role
    sent, effects = [], []
    def serve(request):
        body = json.loads(request.content)
        sent.append(body)
        prompt, messages = body['messages'][0]['content'], body['messages']
        if prompt.startswith('You are a tool permission reviewer'):
            return completion_response(body, text=verdict('ask' if 'Quickly screen' in prompt else 'allow').content)
        if prompt.startswith('Summarize completed conversation history'):
            return completion_response(body, text='Earlier checks complete.')
        if body['model'] == 'child':
            return completion_response(body, text='child report')
        if len([m for m in messages if m['role'] == 'user']) > 1:
            return completion_response(body, text='recent answer')
        results = [m for m in messages if m['role'] == 'tool']
        if not results:
            return completion_response(body, call=('probe', {'value': 'parent'}, 'probe'))
        if len(results) == 1:
            return completion_response(body, call=('subagent', {'agent': 'tester', 'task': 'CHECK'}, 'delegate'))
        return completion_response(body, text='older details ' * 500)
    options = AppSettings(permissions=review_settings(fast_model_id='root', detailed_model_id='root').permissions,
                           subagents=(role(model_id='child'),))
    tools = [definition(calls=effects, approved=True), create_subagent_tool()]
    # Reuse the existing offline native catalog/transport; both production
    # builders see the same callbacks, proving installation remains idempotent.
    async with cached_application(monkeypatch, tmp_path, serve, settings=options, tools=tools):
        async with open_component_application('unused', database_path=tmp_path / 'state.sqlite',
                tools=tools, settings=options, system_prompt='ROOT', response_format=None) as app:
            from naiyue_test_agent.model_access.http_observation import install_http_hooks
            for identity in app.host.sessions._sessions._catalog.model_ids:
                install_http_hooks(app.host.sessions._sessions._catalog.get(identity).root_async_client._client, asynchronous=True)
            sid = await app.host.sessions.create('root')
            await app.host.runs.start(sid, [('user', 'old task')])
            await app.host.runs.start(sid, [('user', 'recent task')])
            assert (await app.host.runs.compact(sid))['compaction_status'] == 'applied'
            saved = rows(app.host.sessions._persistence.storage)
            assert len(saved) == len(sent) == 8 and len({r['invocation_id'] for r in saved}) == 8
            assert sorted(r['purpose'] for r in saved) == ['agent'] * 5 + ['auto_detailed', 'auto_fast', 'summary']
            child, = [r for r in saved if r['agent_id'] is not None]
            assert child['model_id'] == 'child' and child['agent_path'].startswith('root/tester/')
            assert child['parent_run_id'] == child['run_id'] and child['parent_tool_call_id'] == 'delegate'
            assert all(r['status'] == 'returned' and r['input_tokens'] == 100 for r in saved)
            assert all(type(r['duration_ms']) is int and r['duration_ms'] >= 0 for r in saved)
            assert all(r['capture_incomplete'] == 0 for r in app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_model_usage_runs'))
            summary = await app.host.observability.usage(sid, model_limit=1)
            assert summary['recorded_calls'] == len(sent) == 8
            assert summary['metrics']['input_tokens']['known_sum'] == '800'
            assert summary['metrics']['output_tokens']['known_sum'] == '80'
            assert summary['duration'] == {'known_sum_ms': str(sum(r['duration_ms'] for r in saved)),
                                           'reported_calls': 8, 'unknown_calls': 0}
            assert summary['capture']['incomplete_runs'] == 0
            assert summary['models']['next_after'] is not None
            page = await app.host.observability.usage(sid, model_limit=1, after_model=summary['models']['next_after'])
            assert page['metrics'] == summary['metrics'] and len(sent) == 8
            assert not (set(r['model_id'] for r in page['models']['items']) &
                        set(r['model_id'] for r in summary['models']['items']))
            assert effects == [('probe', 'parent')]
            await app.host.observability._diagnostics.aclose()
            captures = (await app.host.observability.runs(sid))['runs']
            assert len(captures) == 3 and all(not r['capture_incomplete'] for r in captures)
            assert all(type(r['duration_ms']) is int and r['duration_ms'] >= 0 for r in captures)
            diagnostic_events = []
            for capture in captures:
                diagnostic_events.extend((await app.host.observability.events(sid, capture['run_id'], limit=100))['events'])
            starts = [e for e in diagnostic_events if e['event_type'] == 'model_call_started']
            assert len(starts) == len(sent) == 8
            assert {e['data']['purpose'] for e in starts} == {'agent', 'summary', 'auto_fast', 'auto_detailed'}
            assert any(e['source'] and e['source']['agent_run_id'] for e in starts)
            assert any(e['event_type'] == 'compaction_finished' for e in diagnostic_events)
            assert sum(r['http_attempts']['observed_attempts'] for r in captures) == len(sent) == 8
            assert all(r['http_attempts']['status'] == 'complete' for r in captures)
            http = [e for e in diagnostic_events if e['event_type'] == 'model_http_attempt_started']
            assert len(http) == 8 and any(e['source'] for e in http)
            finishes = [e for e in diagnostic_events if e['event_type'] in
                        ('model_call_finished', 'tool_call_finished', 'run_finished')]
            assert all(type(e['data']['duration_ms']) is int and e['data']['duration_ms'] >= 0 for e in finishes)
            await app.host.sessions.delete(sid)
            assert rows(app.host.sessions._persistence.storage) == [] and app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_model_usage_runs') == []
            assert app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_diagnostic_runs') == []


@async_test
async def test_input_overflow_saves_three_calls_with_failure_and_summary(monkeypatch, tmp_path):
    from test_context_overflow_recovery import _configured_model, _history, _status
    model = _configured_model([_status(), AIMessage(content='done')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        await app.host.runs.start(await app.host.sessions.create('A'), _history())
        saved = rows(app.host.sessions._persistence.storage)
        assert len(saved) == 3
        assert sorted(r['purpose'] for r in saved) == ['agent', 'agent', 'summary']
        assert sorted(r['status'] for r in saved) == ['failed', 'returned', 'returned']
        assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1


@async_test
async def test_healthy_session_finishes_while_other_model_waits_and_stop_persists_cancel(monkeypatch, tmp_path):
    import asyncio
    from test_async_application import StreamingToolModel
    entered, release = asyncio.Event(), asyncio.Event()
    blocked = StreamingToolModel(replies=[AIMessage(content='never')], entered=entered, release=release)
    async with build(monkeypatch, tmp_path / 'state.sqlite', blocked) as app:
        a, b = await app.host.sessions.create('A'), await app.host.sessions.create('A')
        task = asyncio.create_task(app.host.runs.start(a, [('user', 'hold')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            blocked.entered = blocked.release = None
            assert (await asyncio.wait_for(app.host.runs.start(b, [('user', 'finish')]), 5))['messages'][-1].content == 'never'
            assert not task.done()
            app.host.runs.stop(a)
            assert (await asyncio.wait_for(task, 5))['run_stopped']
            saved = rows(app.host.sessions._persistence.storage)
            assert len(saved) == 2 and {r['status'] for r in saved} == {'returned', 'cancelled'}
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@async_test
async def test_direct_close_drains_writer_and_initialization_failure_leaves_no_task(monkeypatch, tmp_path):
    from naiyue_test_agent.application.observability.model_usage_persistence import ModelUsagePersistence
    from naiyue_test_agent.application.sessions.session_service import SessionService
    captured = []
    original = ModelUsagePersistence.__init__
    def record(self, *args):
        original(self, *args)
        captured.append(self)
    monkeypatch.setattr(ModelUsagePersistence, '__init__', record)
    async with build(monkeypatch, tmp_path / 'normal.sqlite') as app:
        await app.aclose()
        assert captured[-1]._task.done() and app.host.sessions._sessions._events._handlers == {}
    failure = RuntimeError('PRIVATE_STARTUP')
    def broken(*args, **kwargs):
        raise failure
    monkeypatch.setattr(SessionService, 'assemble_runtime', broken)
    with pytest.raises(RuntimeError) as caught:
        async with build(monkeypatch, tmp_path / 'failed.sqlite'):
            pytest.fail('Runtime startup must fail')
    assert caught.value is failure and captured[-1]._task.done()
