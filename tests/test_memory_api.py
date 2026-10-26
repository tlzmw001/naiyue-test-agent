"""The memory domain owns the real run capability, not an idle-session bypass."""
import asyncio
from dataclasses import replace
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
from naiyue_test_agent.contracts.run_context import RunIdentity, RunScope
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import SubAgentSpec
from naiyue_test_agent.tools.memory import create_memory_tool
from naiyue_test_agent.tools.subagent import create_subagent_tool
from naiyue_test_agent.tools.permissions import ToolPermission
from test_async_application import StreamingToolModel, call, make_app
from test_memory_tool import opened, feedback


def test_temporary_api_is_present_unavailable_and_closed_with_owner(monkeypatch, tmp_path):
    async def scenario():
        app = await make_app(monkeypatch, StreamingToolModel(replies=[]), [create_memory_tool()])
        try:
            assert app.host.memory._owner is app.host.sessions._owner and app.host.memory._owner._parent is app.root._lifetime and app.host.memory._access is None
            with pytest.raises(ToolException, match='memory_unavailable'):
                await app.host.memory.query(scope=RunScope(RunIdentity('absent')), request={'action': 'list'})
            assert not (tmp_path / '.naiyue').exists()
        finally:
            await app.aclose()
        with pytest.raises(RuntimeError, match='owning event loop'):
            await app.host.memory.query(scope=RunScope(RunIdentity('absent')), request={'action': 'list'})
    asyncio.run(scenario())


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('approval', [False, True])
def test_formal_root_child_and_approval_use_same_memory_api(monkeypatch, tmp_path, child, approval):
    async def scenario():
        from naiyue_test_agent.application.memory.api import MemoryAPI
        from test_subagent_approval_resume import approval_notifications, next_approval, submit
        seen = []
        original = MemoryAPI.query
        async def traced(self, **kwargs):
            scope = kwargs['scope']
            seen.append((self, scope, current_run_scope()))
            if child:
                for identity in (replace(scope.identity, run_id=scope.identity.parent_run_id),
                                 replace(scope.identity, parent_tool_call_id='foreign'),
                                 replace(scope.identity, root_run_id='foreign')):
                    with pytest.raises(ToolException, match='memory_scope_mismatch'):
                        await original(self, **(kwargs | {'scope': replace(scope, identity=identity)}))
            return await original(self, **kwargs)
        monkeypatch.setattr(MemoryAPI, 'query', traced)
        reader = StreamingToolModel(replies=[call('memory', {'action': 'list'}, 'memory-call'), AIMessage(content='read')])
        root = (StreamingToolModel(replies=[call('subagent', {'agent': 'tester', 'task': 'read'}), AIMessage(content='done')])
                if child else reader)
        settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')}})
        memory_tool = create_memory_tool()
        if approval:
            memory_tool = replace(memory_tool, permissions=ToolPermission(default='ask'))
        if child:
            settings = settings.model_copy(update={'subagents': (
                SubAgentSpec('tester', 'Read memory', 'Read saved records.', ('memory',), model_id='B'),)})
        async with opened(monkeypatch, tmp_path, root, settings=settings,
                tools=[memory_tool, create_subagent_tool()], models={'A': root, 'B': reader}) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            queue = approval_notifications(monkeypatch, app) if child and approval else None
            task = asyncio.create_task(app.host.runs.start(sid, [('user', 'read')]))
            try:
                if child and approval:
                    batch = await next_approval(app, sid, queue)
                    assert seen == []
                    await submit(app, sid, batch)
                result = await task
                if approval and not child:
                    assert result['approval_requests'] and seen == []
                    result = await app.host.runs.resume_approval(sid, ['approve'])
                message = (next(m for m in reader.seen[-1] if isinstance(m, ToolMessage) and m.name == 'memory')
                           if child else feedback(result)[0])
                assert message.status == 'success' and json.loads(message.content)['items'] == []
                api, scope, bound = seen[0]
                assert len(seen) == 1 and api is app.host.memory
                assert scope.capabilities.memory.__self__ is app.host.memory and scope is bound
                assert scope.identity.session_id == sid and scope.identity.project_id is not None
                assert (scope.identity.agent_path != 'root') is child
                assert not hasattr(app.host.sessions._persistence, 'aquery_memory')
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('enabled', [False, True])
def test_unconfigured_memory_api_uses_no_resources(monkeypatch, tmp_path, enabled):
    async def scenario():
        settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')},
                                               'memory': {'enabled': enabled}})
        tools = [create_subagent_tool()] if enabled else [create_memory_tool()]
        async with opened(monkeypatch, tmp_path, StreamingToolModel(replies=[]), settings=settings, tools=tools) as app:
            assert app.host.memory._owner is app.host.sessions._owner and app.host.memory._owner._parent is app.root._lifetime and app.host.memory._access is None and app.host.memory._jobs is None
            with pytest.raises(ToolException, match='memory_unavailable'):
                await app.host.memory.query(scope=RunScope(RunIdentity('absent')), request={'action': 'list'})
        assert not (tmp_path / 'data/personal/memory.sqlite').exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', ['old_run', 'session', 'project', 'root', 'role', 'publisher', 'inactive', 'schema', 'missing'])
def test_api_rejects_foreign_stale_inactive_and_invalid_requests(monkeypatch, tmp_path, invalid):
    async def scenario():
        from naiyue_test_agent.application.memory.api import MemoryAPI
        from naiyue_test_agent.execution_roots import ExecutionRoot
        original = MemoryAPI.query
        checked, queries = [], []
        def unexpected_query(*args, **kwargs):
            queries.append(args)
            raise AssertionError('rejected API must not reach storage')
        async def check(self, *, scope, request, **kwargs):
            identity = scope.identity
            # Construct invalid callers without tripping the independent notice identity guard.
            broken = replace(scope, background_notices=None)
            payload = request
            if invalid == 'old_run': broken = replace(broken, identity=replace(identity, run_id='old'))
            elif invalid == 'session': broken = replace(broken, identity=replace(identity, session_id='absent'))
            elif invalid == 'project': broken = replace(broken, identity=replace(identity, project_id='other'))
            elif invalid == 'root': broken = replace(broken, identity=replace(identity, execution_root=ExecutionRoot(tmp_path / 'other')))
            elif invalid == 'role': broken = replace(broken, identity=replace(identity, agent_path='root/absent/child'))
            elif invalid == 'publisher': broken = replace(broken, observation=type(scope.observation)())
            elif invalid == 'inactive': await app.host.sessions.select_tools(identity.session_id, [])
            elif invalid == 'schema': payload = {'action': 'list', 'project_id': 'other'}
            elif invalid == 'missing': (tmp_path / '.naiyue/workspace.json').unlink()
            with monkeypatch.context() as guard:
                guard.setattr(self._access, 'query', unexpected_query)
                with pytest.raises(ToolException, match=('Invalid memory arguments' if invalid == 'schema' else
                        'execution_root_unavailable' if invalid == 'missing' else 'memory_scope_mismatch')):
                    await original(self, scope=broken, request=payload, **kwargs)
            checked.append(True)
            return {'items': []}
        monkeypatch.setattr(MemoryAPI, 'query', check)
        model = StreamingToolModel(replies=[call('memory', {'action': 'list'}), AIMessage(content='done')])
        async with opened(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            result = await app.host.runs.start(sid, [('user', 'read')])
            assert checked == [True] and feedback(result)[0].status == 'success'
            assert queries == []
    asyncio.run(scenario())


def test_api_rejects_finished_scope_during_next_run(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.application.memory.api import MemoryAPI
        captured = []
        original = MemoryAPI.query
        async def check(self, *, scope, request, **kwargs):
            if captured:
                with pytest.raises(ToolException, match='memory_scope_mismatch'):
                    await original(self, scope=captured[0], request=request)
            captured.append(scope)
            return await original(self, scope=scope, request=request, **kwargs)
        monkeypatch.setattr(MemoryAPI, 'query', check)
        model = StreamingToolModel(replies=[call('memory', {'action': 'list'}), AIMessage(content='one'),
                                           call('memory', {'action': 'list'}), AIMessage(content='two')])
        async with opened(monkeypatch, tmp_path, model) as app:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            for text in ('one', 'two'):
                assert feedback(await app.host.runs.start(sid, [('user', text)]))[0].status == 'success'
                with pytest.raises(ToolException, match='memory_scope_mismatch'):
                    await original(app.host.memory, scope=captured[-1], request={'action': 'list'})
            assert len(captured) == 2 and captured[0].identity.run_id != captured[1].identity.run_id
    asyncio.run(scenario())
