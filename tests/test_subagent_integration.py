"""Complete file-tool lifecycle and SDK thread fallback through the real app."""

import asyncio
from threading import Event, get_ident
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from native_application_helpers import async_test
from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime.run_control import native_async_model
from naiyue_test_agent.agent_runtime.run_services import current_run_services
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import (SubAgentSpec, ToolSettings, create_subagent_tool,
                                    create_session_read_tool, create_session_write_tool)
from test_async_application import StreamingToolModel, call
from test_subagent_execution import delegated_app, record_instances
from test_subagent_approval_resume import cancel_run
from test_dynamic_tools import definition


@async_test
async def test_real_read_write_report_ready_reopen_and_new_delegation(monkeypatch, tmp_path, caplog):
    workspace = tmp_path / 'work'
    workspace.mkdir()
    (workspace / 'seed.txt').write_text('leaf input stays in the child', encoding='utf-8')
    definition = SubAgentSpec('writer', 'Write and verify a file', 'Use the file tools and report.',
                              ('read', 'write'), model_id='B', max_model_calls=4)
    options = dict(database_path=tmp_path / 'state.sqlite',
        tools=[create_subagent_tool(), create_session_read_tool(), create_session_write_tool()],
        initial_active_tools=['subagent'], system_prompt='', response_format=None,
        settings=ToolSettings(subagents=(definition,)))
    identities, saved_root = [], None
    for round_number in (1, 2):
        from test_subagent_execution import ChildModel
        body, report = f'actual output {round_number}', f'verified report {round_number}'
        child = ChildModel(replies=[call('read', {'path': 'seed.txt'}, 'read-seed'),
            call('write', {'path': 'nested/output.txt', 'content': body}, 'write-output'),
            call('read', {'path': 'nested/output.txt'}, 'read-output'), AIMessage(content=report)])
        root = StreamingToolModel(replies=[call('subagent', {'agent': 'writer', 'task': f'round {round_number}'}, 'outer'),
            AIMessage(content=f'root done {round_number}')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
        async with open_async_persistent_application('unused', **options) as app:
            instances = record_instances(monkeypatch, app)
            events = []
            app.subscribe_events(events.append)
            if round_number == 1:
                sid = await app.acreate_workspace_session('A', workspace)
            else:
                assert await app.arestore_session(sid) == sid
                assert await app.aget_messages(sid) == saved_root
                assert all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values())
            result = await app.arun(sid, [('user', f'check round {round_number}')])
            saved_root = await app.aget_messages(sid)
            assert saved_root == result['messages']
            assert (workspace / 'nested/output.txt').read_text(encoding='utf-8') == body
            instance, = instances
            identities.append(instance['agent_path'])
            assert instance['status'] == 'completed' and instance['settled']
            assert all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values()) and app.pending_approval_batches(sid) == []
            graph = app._sessions._subagents['writer']['agent']
            snapshot = await graph.aget_state({'configurable': {'thread_id': instance['child_thread_id']}})
            assert not snapshot.next and not snapshot.interrupts
            messages = snapshot.values['messages']
            assert snapshot.values['thread_model_call_count'] == 4
            assert [m.content for m in messages if isinstance(m, HumanMessage)] == [f'round {round_number}']
            assert [m.name for m in messages if isinstance(m, ToolMessage)] == ['read', 'write', 'read']
            assert next(m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == 'read-output').content == body
            feedback = [m for m in saved_root if isinstance(m, ToolMessage)][-1]
            assert feedback.status == 'success' and feedback.content == report
            assert feedback.artifact['agent_path'] == instance['agent_path']
            assert feedback.artifact['status'] == 'completed'
            assert 'leaf input stays in the child' not in str(saved_root)
            assert not any(m.name in ('read', 'write') for m in saved_root if isinstance(m, ToolMessage))
            row, = await app.alist_sessions()
            assert row['session_id'] == sid and row['state'] == 'ready' and row['last_run_status'] == 'completed'
            assert [(e.source is None, e.data.status) for e in events if e.event_type == 'run_finished'] == [
                (False, 'completed'), (True, 'completed')]
    assert identities[0] != identities[1]
    assert not any(marker in caplog.text for marker in (
        'observation_failed', 'run_event_publish_failed', 'message_stream_close_failed'))


class SyncChildModel(BaseChatModel):
    loop: Any
    entered: Any
    release: Any
    trace: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return 'sync-sdk-fallback-test'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        services = current_run_services()
        self.trace.append(('sdk_started', get_ident(), services.publisher.source.agent_path))
        self.loop.call_soon_threadsafe(self.entered.set)
        assert self.release.wait(5)
        self.trace.append(('sdk_finished', get_ident(), services.publisher.source.agent_path))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='late SDK response'))])


@pytest.mark.parametrize('action', ['child_stop', 'parent_cancel', 'close'])
@async_test
async def test_sync_sdk_fallback_waits_for_real_worker_before_releasing_owner(monkeypatch, action):
    entered, notified = asyncio.Event(), asyncio.Event()
    release = Event()
    child = SyncChildModel(loop=asyncio.get_running_loop(), entered=entered, release=release)
    assert not native_async_model(child)
    app, root = await delegated_app(monkeypatch, child, tools=[definition()])
    instances, events, closed = record_instances(monkeypatch, app), [], []
    app.subscribe_events(events.append)
    close = app._catalog.aclose
    async def close_catalog():
        closed.append('closed')
        await close()
    monkeypatch.setattr(app._catalog, 'aclose', close_catalog)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'run sync SDK')]))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        instance, = instances
        request = instance['control'].request
        def notify(reason='user'):
            request(reason)
            notified.set()
        monkeypatch.setattr(instance['control'], 'request', notify)
        if action == 'parent_cancel': task.cancel()
        elif action == 'close': closing = asyncio.create_task(app.aclose())
        else: app.request_stop(sid, agent_path=instance['agent_path'])
        await asyncio.wait_for(notified.wait(), 5)
        assert not task.done() and app._sessions.describe_session(sid)['busy']
        assert not instance['settled'] and 'driver' in instance and not closed
        assert child.trace == [('sdk_started', child.trace[0][1], instance['agent_path'])]
        assert child.trace[0][1] != get_ident()
        release.set()
        if action == 'parent_cancel':
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 5)
        else:
            result = await asyncio.wait_for(task, 5)
            if action == 'close': assert result['run_stopped']
            else: assert result['messages'][-1].content == 'root done' and len(root.seen) == 2
        if closing is not None: await asyncio.wait_for(closing, 5)
        assert [item[0] for item in child.trace] == ['sdk_started', 'sdk_finished']
        assert instance['settled'] and instance['status'] == 'stopped' and 'driver' not in instance
        assert all(not (owner._run.subagent_instances if owner._run else {}) for owner in app._sessions._sessions.values()) and not app._sessions.describe_session(sid)['busy']
        assert current_run_services() is None
        assert not any(e.source and e.event_type == 'run_finished' and e.data.status == 'completed' for e in events)
        assert closed == (['closed'] if action == 'close' else [])
    finally:
        release.set()
        await cancel_run(task)
        if closing is not None: await asyncio.gather(closing, return_exceptions=True)
