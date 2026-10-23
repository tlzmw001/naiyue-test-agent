"""MCP execution uses the real child graph, scoped approval and owned resources."""

from runtime_context_helpers import typed_scope
import json
import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import SubAgentSpec, create_subagent_tool
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls, make_mcp_app
from test_mcp_connections import facts, process_alive, stdio_settings, until
from test_subagent_execution import record_instances


class MCPChildModel(StreamingToolModel):
    on_request: Any = None

    async def _next(self, messages):
        if self.on_request is not None:
            await self.on_request(messages)
        return await super()._next(messages)


def child_settings(path, *, mode='Default', names=(NAME,), server=None):
    return AppSettings.model_validate({'mcp': {'servers': {'local': (server or stdio_settings(
        path, 'stateful', allowed_tools=['echo'])).model_dump()}}, 'permissions': {'mode': mode},
        'subagents': [dict(name='tester', description='Test MCP', system_prompt='Use explicitly selected tools',
                          tool_names=names, model_id='B')]})


async def make_child_app(monkeypatch, path, child, *, root=None, mode='Default', names=(NAME,), server=None):
    root = root or StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'check'}, 'outer'),
                                               AIMessage(content='root done')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': root, 'B': child}))
    app = await build_async_local_application('unused', tools=[create_subagent_tool()],
        initial_active_tools=['subagent', NAME], system_prompt='', response_format=None,
        settings=child_settings(path, mode=mode, names=names, server=server))
    return app, root


def test_root_and_child_have_independent_processes_and_child_reuses_until_finished(monkeypatch, tmp_path):
    async def scenario():
        path = tmp_path / 'child.jsonl'
        child = MCPChildModel(replies=[call(NAME, {'text': 'one'}, 'a'), call(NAME, {'text': 'two'}, 'b'), AIMessage(content='child report')])
        root = StreamingToolModel(replies=[call(NAME, {}, 'root-call'),
            call('subagent', {'agent': 'tester', 'task': 'two calls'}, 'outer'), AIMessage(content='root done')])
        app, _ = await make_child_app(monkeypatch, path, child, root=root, mode='BypassApprovals')
        instances = record_instances(monkeypatch, app)
        try:
            sid = await app.acreate_session('A')
            result = await app.arun(sid, [('user', 'root and child')])
            submitted = calls(path)
            assert [r['count'] for r in submitted] == [1, 1, 2]
            assert submitted[0]['pid'] != submitted[1]['pid'] == submitted[2]['pid']
            assert process_alive(submitted[0]['pid']) and not process_alive(submitted[1]['pid'])
            assert result['messages'][-1].content == 'root done'
            instance, = instances
            assert instance['settled'] and instance['status'] == 'completed'
            from naiyue_test_agent.mcp_client.resources import MCPResourceError
            with pytest.raises(MCPResourceError, match='instance_not_active'):
                async with app._sessions.borrow_mcp(sid, instance['agent_path'], 'local'):
                    pytest.fail('finished instances cannot recreate connections')
        finally:
            await app.aclose()
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())


def test_child_publisher_cannot_borrow_root_connection(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime.events import AgentEventSource
    from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope
    from naiyue_test_agent.mcp_client.resources import MCPResourceError
    async def scenario():
        path = tmp_path / 'scope.jsonl'
        app, _ = await make_mcp_app(monkeypatch, path, [])
        try:
            sid = await app.acreate_session('A')
            publisher = app._sessions._events.publisher(sid, 'root-run').for_agent(
                AgentEventSource('root/tester/one', 'one', 'child-run', 'root-run', 'outer'))
            with bind_run_scope(typed_scope(publisher=publisher)):
                with pytest.raises(MCPResourceError, match='scope_mismatch'):
                    async with app._sessions.borrow_mcp(sid, 'root', 'local'):
                        pytest.fail('a child publisher cannot lend its control to root')
            assert len([r for r in facts(path) if r['event'] == 'start']) == 1
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('remember', [False, True])
def test_live_approval_preserves_existing_child_connection_and_remember_scope(monkeypatch, tmp_path, remember):
    from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
    async def scenario():
        path = tmp_path / 'approval.jsonl'
        child = MCPChildModel(replies=[call(NAME, {'text': 'first'}, 'a'),
            call(NAME, {'text': 'different args'}, 'b'), AIMessage(content='child report')])
        root_replies = [call('subagent', {'agent': 'tester', 'task': 'two calls'}, 'outer')]
        if remember:
            root_replies.append(call(NAME, {'text': 'root different args'}, 'root-call'))
        root = StreamingToolModel(replies=[*root_replies, AIMessage(content='root done')])
        app, _ = await make_child_app(monkeypatch, path, child, root=root)
        instances = record_instances(monkeypatch, app)
        queue = approval_notifications(monkeypatch, app)
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'two calls')]))
        try:
            first = await next_approval(app, sid, queue)
            assert not calls(path) and first['items'][0]['remember_scope'] == 'tool'
            await submit(app, sid, first, remember=[first['items'][0]['approval_id']] if remember else [])
            if not remember:
                second = await next_approval(app, sid, queue)
                assert len(calls(path)) == 1
                pid = calls(path)[0]['pid']
                assert process_alive(pid) and not task.done()
                assert second['agent_path'] == first['agent_path']
                owner, = [owner for key, owner in app._sessions.mcp_resources._owners.items()
                          if key[0] == sid and key[1] == first['agent_path']]
                assert not owner.invalid
                await submit(app, sid, second)
            result = await asyncio.wait_for(task, 15)
            assert [row['count'] for row in calls(path)] == ([1, 2, 1] if remember else [1, 2])
            assert calls(path)[0]['pid'] == calls(path)[1]['pid']
            assert not process_alive(calls(path)[0]['pid'])
            assert instances[0]['settled'] and app.pending_subagent_approvals(sid) == []
            assert result['messages'][-1].content == 'root done'
            if remember:
                assert app._sessions.resolve_permission_context(sid)['session_allow'][0]['tool_name'] == NAME
                assert calls(path)[-1]['pid'] != calls(path)[0]['pid']
        finally:
            await cancel_run(task)
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('case', ['drop', 'timeout', 'error', 'media', 'interaction', 'unselected'])
def test_child_tool_error_is_feedback_and_both_models_continue(monkeypatch, tmp_path, case):
    async def scenario():
        path = tmp_path / 'error.jsonl'
        action = 'wait' if case == 'timeout' else case
        child = MCPChildModel(replies=[call(NAME, {'text': action}), AIMessage(content='child handled error')])
        server = stdio_settings(path, 'stateful' if case in ('drop', 'timeout') else 'tools',
            allowed_tools=['echo'], call_timeout_seconds=1. if case == 'timeout' else 0.)
        app, root = await make_child_app(monkeypatch, path, child, mode='BypassApprovals', server=server,
                                         names=() if case == 'unselected' else (NAME,))
        try:
            sid = await app.acreate_session('A')
            result = await app.arun(sid, [('user', case)])
            inner, = [m for m in child.seen[-1] if isinstance(m, ToolMessage)]
            assert inner.status == 'error'
            assert len(calls(path)) == (0 if case == 'unselected' else 1)
            outer, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert json.loads(outer.content)['report'] == 'child handled error' and outer.status == 'success'
            assert result['messages'][-1].content == 'root done' and len(root.seen) == 2
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('scope', ['child', 'root', 'close'])
def test_stop_inflight_child_call_settles_resources_without_replay(monkeypatch, tmp_path, scope):
    from test_subagent_approval_resume import cancel_run
    async def scenario():
        path = tmp_path / 'stop.jsonl'
        child = MCPChildModel(replies=[call(NAME, {'text': 'wait'}), AIMessage(content='must not finish')])
        app, _ = await make_child_app(monkeypatch, path, child, mode='BypassApprovals')
        instances = record_instances(monkeypatch, app)
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'wait')]))
        try:
            await until(lambda: len(calls(path)) == 1)
            instance, = instances
            if scope == 'close':
                await asyncio.wait_for(app.aclose(), 15)
            else:
                app.request_stop(sid, agent_path=instance['agent_path'] if scope == 'child' else 'root')
            result = await asyncio.wait_for(task, 15)
            assert len(calls(path)) == 1 and not process_alive(calls(path)[0]['pid'])
            assert instance['settled'] and instance['status'] == 'stopped'
            # The owner can be inspected after close; the public app API is closed.
            assert app._sessions.require(sid).pending_subagent_approvals() == []
            if scope == 'child':
                assert result['messages'][-1].content == 'root done' and not result.get('run_stopped')
            else:
                assert result['run_stopped']
        finally:
            await cancel_run(task)
            await app.aclose()
    asyncio.run(scenario())


def test_stop_pending_child_after_first_call_closes_retained_connection(monkeypatch, tmp_path):
    from test_subagent_approval_resume import approval_notifications, next_approval, submit, cancel_run
    async def scenario():
        path = tmp_path / 'pending-stop.jsonl'
        child = MCPChildModel(replies=[call(NAME, {}, 'a'), call(NAME, {'text': 'second'}, 'b')])
        app, _ = await make_child_app(monkeypatch, path, child)
        queue = approval_notifications(monkeypatch, app)
        instances = record_instances(monkeypatch, app)
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'two calls')]))
        try:
            first = await next_approval(app, sid, queue)
            await submit(app, sid, first)
            second = await next_approval(app, sid, queue)
            assert len(calls(path)) == 1 and process_alive(calls(path)[0]['pid'])
            app.request_stop(sid, agent_path=second['agent_path'])
            result = await asyncio.wait_for(task, 15)
            assert result['messages'][-1].content == 'root done' and len(calls(path)) == 1
            assert not process_alive(calls(path)[0]['pid']) and instances[0]['settled']
            with pytest.raises(ValueError): await submit(app, sid, second)
        finally:
            await cancel_run(task)
            await app.aclose()
    asyncio.run(scenario())


def test_stop_queued_child_keeps_other_session_owner_alive(monkeypatch, tmp_path):
    from test_subagent_approval_resume import cancel_run
    async def scenario():
        path = tmp_path / 'queued.jsonl'
        child = MCPChildModel(replies=[call(NAME, {}), AIMessage(content='must not finish')])
        app, _ = await make_child_app(monkeypatch, path, child, mode='BypassApprovals')
        instances = record_instances(monkeypatch, app)
        sid, other = await app.acreate_session('A'), await app.acreate_session('A')
        try:
            async with app._sessions.borrow_mcp(other, 'root', 'local'):
                pid = [r['pid'] for r in facts(path) if r['event'] == 'start'][-1]
                task = asyncio.create_task(app.arun(sid, [('user', 'queued')]))
                try:
                    await until(lambda: len(app._sessions.mcp_resources._requests) == 2)
                    app.request_stop(sid, agent_path=instances[0]['agent_path'])
                    result = await asyncio.wait_for(task, 15)
                    assert result['messages'][-1].content == 'root done' and not calls(path)
                    assert process_alive(pid) and instances[0]['settled']
                finally:
                    await cancel_run(task)
        finally:
            await app.aclose()
        assert not process_alive(pid)
    asyncio.run(scenario())


def test_stopping_one_child_does_not_stop_sibling_or_close_its_client(monkeypatch, tmp_path):
    from test_subagent_approval_resume import cancel_run
    async def scenario():
        path = tmp_path / 'siblings.jsonl'
        b_entered, b_release = asyncio.Event(), asyncio.Event()
        pids = {}
        class SiblingModel(StreamingToolModel):
            async def _next(self, messages):
                self.seen.append(messages)
                task_name = next(m.content for m in messages if m.type == 'human')
                if isinstance(messages[-1], ToolMessage):
                    if task_name == 'B':
                        pids['B'] = messages[-1].artifact['structured_content']['pid']
                        b_entered.set()
                        await b_release.wait()
                    return AIMessage(content=task_name + ' report')
                return call(NAME, {'text': 'wait' if task_name == 'A' else 'normal'}, 'same-inner')
        child = SiblingModel(replies=[])
        root = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'subagent', 'args': {'agent': 'tester', 'task': name}, 'id': name} for name in ('A', 'B')]), AIMessage(content='root done')])
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'], max_concurrency=2)
        app, _ = await make_child_app(monkeypatch, path, child, root=root, mode='BypassApprovals', server=server)
        instances = record_instances(monkeypatch, app)
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'siblings')]))
        try:
            await asyncio.wait_for(b_entered.wait(), 15)
            await until(lambda: len(calls(path)) == 2)
            a = next(i for i in instances if i['task'] == 'A')
            b = next(i for i in instances if i['task'] == 'B')
            a_pid, = {r['pid'] for r in calls(path)} - {pids['B']}
            app.request_stop(sid, agent_path=a['agent_path'])
            await until(lambda: a['settled'])
            assert not process_alive(a_pid) and process_alive(pids['B'])
            assert not b['control'].requested and not task.done()
            b_release.set()
            result = await asyncio.wait_for(task, 15)
            feedback = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
            assert feedback['A'].status == 'error' and json.loads(feedback['B'].content)['report'] == 'B report'
            assert result['messages'][-1].content == 'root done' and len(calls(path)) == 2
            assert not process_alive(pids['B'])
        finally:
            b_release.set()
            await cancel_run(task)
            await app.aclose()
    asyncio.run(scenario())


def test_stop_child_while_its_connection_catalog_is_still_loading(monkeypatch, tmp_path):
    from test_subagent_approval_resume import cancel_run
    async def scenario():
        path = tmp_path / 'connecting.jsonl'
        child = MCPChildModel(replies=[call(NAME, {})])
        app, _ = await make_child_app(monkeypatch, path, child, mode='BypassApprovals')
        instances = record_instances(monkeypatch, app)
        path.with_suffix('.block-catalog').touch()
        sid = await app.acreate_session('A')
        task = asyncio.create_task(app.arun(sid, [('user', 'connecting')]))
        try:
            await until(lambda: len([r for r in facts(path) if r['event'] == 'list']) >= 3)
            app.request_stop(sid, agent_path=instances[0]['agent_path'])
            result = await asyncio.wait_for(task, 15)
            assert not calls(path) and result['messages'][-1].content == 'root done'
            assert instances[0]['settled']
            assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
        finally:
            await cancel_run(task)
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('json_response', [False, True])
def test_child_http_sdk_call_reuses_client_and_closes_at_instance_end(monkeypatch, tmp_path, json_response):
    from test_mcp_connections import http_server
    from naiyue_test_agent.mcp_client.connections import _BudgetTransport
    from naiyue_test_agent.mcp_client.settings import MCPServerSettings
    async def scenario():
        path = tmp_path / 'http-child.jsonl'
        closed = []
        close = _BudgetTransport.aclose
        async def tracked(self):
            await close(self)
            closed.append(id(self))
        monkeypatch.setattr(_BudgetTransport, 'aclose', tracked)
        async with http_server(path, 'stateful', json_response, legacy=True) as url:
            server = MCPServerSettings(enabled=True, transport='streamable_http', url=url, allowed_tools=['echo'])
            child = MCPChildModel(replies=[call(NAME, {}, 'a'), call(NAME, {}, 'b'), AIMessage(content='HTTP report')])
            app, _ = await make_child_app(monkeypatch, path, child, mode='BypassApprovals', server=server)
            try:
                sid = await app.acreate_session('A')
                result = await app.arun(sid, [('user', 'HTTP child')])
                assert result['messages'][-1].content == 'root done'
                assert [r['count'] for r in calls(path)] == [1, 2]
                assert len(closed) == 2 and app._sessions.mcp_resources._owners == {}
                assert len([r for r in facts(path) if r['event'] == 'delete']) == 2
            finally:
                await app.aclose()
    asyncio.run(scenario())
