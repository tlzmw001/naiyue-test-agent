"""Review waits share the run's stop, cancellation and session isolation contracts."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel
from test_auto_permission_review import model, settings, verdict
from test_dynamic_tools import call, definition


async def build(monkeypatch, primary, fast, detailed, executed):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog(
        'main', {'main': primary, 'fast': fast, 'detail': detailed}))
    return await build_async_local_application('synthetic', tools=[definition(approved=True, calls=executed)],
        settings=settings(), system_prompt='', response_format=None)


@pytest.mark.parametrize('stage', ['fast', 'detailed'])
def test_stop_review_pairs_calls_and_never_executes_or_leaves_an_interrupt(monkeypatch, stage):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        waiting = StreamingToolModel(replies=[verdict()], entered=entered, release=release, metadata=model([]).metadata)
        fast = waiting if stage == 'fast' else model([verdict('ask')])
        detailed = waiting if stage == 'detailed' else model([])
        executed, events = [], []
        app = await build(monkeypatch, StreamingToolModel(replies=[call()]), fast, detailed, executed)
        sid = await app.acreate_session('main')
        app.subscribe_events(events.append)
        task = asyncio.create_task(app.arun(sid, [('user', 'run probe')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            app.request_stop(sid)
            result = await asyncio.wait_for(task, 5)
            assert result['run_stopped'] is True and '__interrupt__' not in result
            assert app.pending_tool_approvals(sid) == [] and executed == []
            paired = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [(m.tool_call_id, m.status) for m in paired] == [('call', 'error')]
            finished = [e.data for e in events if e.event_type == 'tool_call_finished']
            assert len(finished) == 1 and finished[0].outcome == 'not_executed' and finished[0].reason == 'user_stop'
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'stopped'
            row = app._sessions._sessions[sid]
            assert row.is_busy() is False and row.resolve_permission_context()['project_instructions'] is None
            state = await app._agent.aget_state({'configurable': {'thread_id': row.get_thread_id()}})
            assert not state.next and not any(t.interrupts for t in state.tasks)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_external_cancellation_during_review_preserves_cancellation_and_cleanup(monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        fast = StreamingToolModel(replies=[verdict()], entered=entered, release=release, metadata=model([]).metadata)
        executed, events = [], []
        app = await build(monkeypatch, StreamingToolModel(replies=[call()]), fast, model([]), executed)
        sid = await app.acreate_session('main')
        app.subscribe_events(events.append)
        task = asyncio.create_task(app.arun(sid, [('user', 'run')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel('external cancellation')
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert executed == [] and app.pending_tool_approvals(sid) == []
            row = app._sessions._sessions[sid]
            assert not row.is_busy() and row.snapshot_for_run()['run_id'] is None and row.resolve_permission_context()['project_instructions'] is None
            assert row.describe_session()['manual_review_required'] is True
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'failed'
            assert events[-1].data.failure.category == 'cancelled' and events[-1].data.failure.reason == 'task_cancelled'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_native_async_review_is_not_a_main_model_stream_or_turn(monkeypatch):
    async def scenario():
        fast = StreamingToolModel(replies=[verdict('ask')], metadata=model([]).metadata)
        detailed = StreamingToolModel(replies=[verdict()], metadata=model([]).metadata)
        primary = StreamingToolModel(replies=[call(), AIMessage(content='done')])
        executed, events = [], []
        app = await build(monkeypatch, primary, fast, detailed, executed)
        app.subscribe_events(events.append)
        try:
            sid = await app.acreate_session('main')
            result = await app.arun(sid, [('user', 'run')])
            assert executed == [('probe', 'value')] and result['messages'][-1].content == 'done'
            assert len(primary.seen) == 2 and len(fast.seen) == len(detailed.seen) == 1
            assert [m.type for m in result['messages']] == ['human', 'ai', 'tool', 'ai']
            assert all('bounded reason' not in str(e) for e in events)
            assert ''.join(e.data.text for e in events if e.event_type == 'message_delta') == 'done'
            requests = [e for e in events if e.event_type == 'model_call_started']
            assert len(requests) == 2 and all(e.data.purpose == 'agent' for e in requests)
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_parallel_sessions_use_separate_human_and_project_inputs(monkeypatch, tmp_path):
    async def scenario():
        both_entered, release = asyncio.Event(), asyncio.Event()
        fast = model([verdict(), verdict()])
        async def hold(number):
            if number == 2:
                both_entered.set()
            await release.wait()
        fast.on_request = hold
        primary = StreamingToolModel(replies=[call(), call(), AIMessage(content='done'), AIMessage(content='done')])
        app = await build(monkeypatch, primary, fast, model([]), [])
        roots = [tmp_path / 'one', tmp_path / 'two']
        for i, root in enumerate(roots):
            root.mkdir()
            (root / 'AGENTS.md').write_text(f'PROJECT_{i}')
        sids = [await app.acreate_workspace_session('main', root) for root in roots]
        tasks = [asyncio.create_task(app.arun(sid, [('user', f'TASK_{i}')])) for i, sid in enumerate(sids)]
        try:
            await asyncio.wait_for(both_entered.wait(), 5)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
            assert all(r['messages'][-1].content == 'done' for r in results)
            inputs = [json.loads(messages[-1].content) for messages in fast.seen]
            assert len(inputs) == 2
            for i in range(2):
                data = next(d for d in inputs if d['human_messages'][0]['content'] == f'TASK_{i}')
                assert f'PROJECT_{i}' in data['project_instructions']
                assert f'PROJECT_{1-i}' not in data['project_instructions']
                assert len(data['human_messages']) == 1
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())
