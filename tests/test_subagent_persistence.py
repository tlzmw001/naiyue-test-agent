"""Child checkpoint failure is local; root persistence remains mandatory."""

import asyncio
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from native_application_helpers import async_test
from test_async_application import StreamingToolModel, call
from test_dynamic_tools import definition
from test_subagent_execution import ChildModel, delegated_app, record_instances
from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run


@pytest.mark.parametrize('operation', ['aget_tuple', 'aput', 'aput_writes'])
@async_test
async def test_child_checkpoint_io_failure_is_tool_feedback_without_secret_or_retry(monkeypatch, caplog, operation):
    child = ChildModel(replies=[AIMessage(content='report')])
    app, root = await delegated_app(monkeypatch, child)
    instances, events, attempts = record_instances(monkeypatch, app), [], []
    app.subscribe_events(events.append)
    backend = app._agent.checkpointer
    original = getattr(backend, operation)
    error = TimeoutError('PRIVATE_CHECKPOINT_BODY')
    async def fail(config, *args, **kwargs):
        if config['configurable']['thread_id'].startswith('subagent:'):
            attempts.append(operation)
            raise error
        return await original(config, *args, **kwargs)
    monkeypatch.setattr(backend, operation, fail)
    result = await app.arun(await app.acreate_session('A'), [('user', 'start')])
    feedback = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert feedback.status == 'error' and 'checkpoint' in feedback.content.lower()
    assert result['messages'][-1].content == 'root done' and len(root.seen) == 2
    assert instances[0]['status'] == 'failed' and instances[0]['settled']
    assert 'PRIVATE_CHECKPOINT_BODY' not in str(result) + caplog.text
    assert [e.data.status for e in events if e.source and e.event_type == 'run_finished'] == ['failed']
    terminal, = [e for e in events if e.source and e.event_type == 'run_finished']
    assert terminal.data.failure.reason == 'subagent_checkpoint_failed'
    if operation == 'aget_tuple': assert attempts == [operation]


@async_test
async def test_failed_child_save_does_not_stop_sibling_pending_approval(monkeypatch):
    effects, events = [], []
    child = ChildModel(replies=[AIMessage(content='A report'),
        call('probe', {'value': 'B'}, 'inner'), AIMessage(content='B report')])
    root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'subagent', 'args': {'agent': 'tester', 'task': task}, 'id': 'outer-' + task} for task in ('A', 'B')]),
        AIMessage(content='root continued')])
    app, _ = await delegated_app(monkeypatch, child, root=root, tools=[definition(calls=effects, approved=True)])
    instances = record_instances(monkeypatch, app)
    queue = approval_notifications(monkeypatch, app)
    app.subscribe_events(events.append)
    backend = app._agent.checkpointer
    save = backend.aput
    async def fail_final_a(config, checkpoint, *args, **kwargs):
        a = next((i for i in instances if i['task'] == 'A'), None)
        if a and config['configurable']['thread_id'] == a['child_thread_id'] and any(
                m.type == 'ai' and m.content == 'A report' for m in checkpoint['channel_values'].get('messages', [])):
            raise OSError('PRIVATE_CHILD_DISK')
        return await save(config, checkpoint, *args, **kwargs)
    monkeypatch.setattr(backend, 'aput', fail_final_a)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'two')]))
    try:
        batch = await next_approval(app, sid, queue)
        a = next(i for i in instances if i['task'] == 'A')
        a_task = a.get('driver')
        if a_task: await asyncio.wait((a_task,), timeout=5)
        assert a['status'] == 'failed' and not app._sessions.is_stop_requested(sid)
        assert app.pending_approval_batches(sid) == [batch] and effects == [] and not task.done()
        await submit(app, sid, batch)
        result = await asyncio.wait_for(task, 5)
        reports = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
        assert reports['outer-A'].status == 'error' and 'checkpoint' in reports['outer-A'].content.lower()
        assert reports['outer-B'].content == 'B report' and effects == [('probe', 'B')]
        assert result['messages'][-1].content == 'root continued'
        assert not any(e.data.status == 'completed' for e in events if e.source and
                       e.source.agent_path == a['agent_path'] and e.event_type == 'run_finished')
    finally:
        await cancel_run(task)
