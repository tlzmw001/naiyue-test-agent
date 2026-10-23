"""History configuration, permission and actual Agent-to-archive wiring."""

from dataclasses import replace
from runtime_context_helpers import RunCapabilities, RunIdentity, ToolAuthorization, typed_context
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from naiyue_test_agent.application.session_storage import SessionPersistenceError
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.tools import create_builtin_tools
from naiyue_test_agent.tools.history import HistoryInput, create_history_tool
from naiyue_test_agent.tools.history_settings import HistorySettings
from test_async_application import StreamingToolModel, call, make_app


@asynccontextmanager
async def build(monkeypatch, path, model, tools, *, settings=None, models=None):
    # Explicit defaults keep this feature's tests independent of personal settings/MCP servers.
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', models or {'A': model}))
    from naiyue_test_agent.workspaces import prepare_workspace
    location = prepare_workspace(path.parent, data_dir=path.parent / 'data', database_path=path)
    async with open_async_persistent_application('unused', database_path=path, tools=tools,
            workspace_binding={'workspace_id': location.workspace_id, 'root_path': location.root},
            system_prompt='', response_format=None, settings=settings or AppSettings()) as app:
        yield app


def feedback(result):
    return [m for m in result['messages'] if isinstance(m, ToolMessage) and m.name == 'history'][-1]


@pytest.mark.parametrize('args', [dict(action='search'), dict(action='read'),
    dict(action='list_sessions', query='extra'), dict(action='read', session_id='x', sequence=1),
    dict(action='search', session_id='x', query=' '), dict(action='read', session_id='x', database_path='secret')])
def test_strict_operation_schema(args):
    with pytest.raises(ValidationError):HistoryInput(**args)


def test_history_is_registered_and_budget_settings_are_loaded(tmp_path):
    path = tmp_path / 'settings.toml'
    path.write_text('[tools.history]\ndefault_limit=3\nmax_limit=7\nsearch_batch_messages=4\n')
    settings = load_settings(path)
    assert settings.tools.history.default_limit == 3
    assert settings.tools.history.max_limit == 7
    spec = next(s for s in create_builtin_tools(settings.tools) if s.tool.name == 'history')
    assert spec.side_effects == 'read_only' and spec.execution_mode == 'parallel'
    assert spec.permissions.default == 'allow'
    assert 'runtime' not in spec.input_schema.model_fields
    with pytest.raises(ValidationError):HistorySettings(default_limit=11, max_limit=10)


def test_new_session_lists_searches_reads_and_model_receives_original_text(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='test environment uses venv')])
        async with build(monkeypatch, tmp_path / 'sessions.sqlite', model, [create_history_tool()]) as app:
            old = await app.acreate_workspace_session('A', tmp_path)
            await app.arun(old, [('user', '记住测试环境')])
            new = await app.acreate_workspace_session('A', tmp_path)
            model.replies.extend([call('history', {'action': 'list_sessions'}, 'list'),
                call('history', {'action': 'search', 'session_id': old, 'query': 'venv'}, 'search'),
                call('history', {'action': 'read', 'session_id': old}, 'read'), AIMessage(content='found prior environment')])
            events=[];app.subscribe_events(events.append, session_id=new)
            result = await app.arun(new, [('user', '查之前的测试环境')])
            tools = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [m.tool_call_id for m in tools] == ['list', 'search', 'read']
            assert all(m.status == 'success' for m in tools)
            assert json.loads(tools[0].content)['items'][0]['session_id'] == old
            assert 'test environment uses venv' in feedback(result).content
            assert any(isinstance(m, ToolMessage) and m.tool_call_id == 'read' for m in model.seen[-1])
            assert len(model.seen) == 5
            assert [e.event_type for e in events].count('tool_call_finished') == 3
            assert not app._sessions.require(new).is_busy()
    asyncio.run(scenario())


def test_history_io_failure_returns_paired_error_then_agent_continues(monkeypatch, tmp_path, caplog):
    async def scenario():
        model=StreamingToolModel(replies=[call('history', {'action':'list_sessions'}, 'bad'), AIMessage(content='history unavailable')])
        async with build(monkeypatch, tmp_path/'sessions.sqlite', model,[create_history_tool()]) as app:
            sid=await app.acreate_workspace_session('A',tmp_path)
            def fail(*args,**kwargs):raise SessionPersistenceError('history_read_failed')
            monkeypatch.setattr(app._storage,'read_history_tool',fail)
            result=await app.arun(sid,[('user','query')])
            msg=feedback(result)
            assert msg.status=='error' and msg.tool_call_id=='bad'
            assert 'history_read_failed' in msg.content
            assert result['messages'][-1].content=='history unavailable' and len(model.seen)==2
    asyncio.run(scenario())


def test_in_memory_application_returns_explicit_unavailable(monkeypatch, tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[call('history',{'action':'list_sessions'}),AIMessage(content='unavailable')])
        app=await make_app(monkeypatch,model,[create_history_tool()])
        try:
            sid=await app.acreate_workspace_session('A',tmp_path)
            result=await app.arun(sid,[('user','query')])
            assert feedback(result).status=='error' and 'history_unavailable' in feedback(result).content
            assert len(model.seen)==2
        finally:await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('blocked', ['inactive', 'deny'])
def test_tool_selection_and_deny_prevent_archive_access(monkeypatch, tmp_path, blocked):
    async def scenario():
        model=StreamingToolModel(replies=[call('history',{'action':'list_sessions'}),AIMessage(content='blocked')])
        settings=AppSettings.model_validate({'permissions':{'deny':[{'tool':'history'}]}} if blocked=='deny' else {})
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_history_tool()],settings=settings) as app:
            sid=await app.acreate_workspace_session('A',tmp_path)
            if blocked=='inactive':await app.aset_active_tools(sid,[])
            accesses=[]
            def forbidden(*args,**kwargs):
                accesses.append(True)
                raise AssertionError('Blocked tool must not access the archive')
            monkeypatch.setattr(app._storage,'read_history_tool',forbidden)
            result=await app.arun(sid,[('user','query')])
            assert feedback(result).status=='error'
            assert accesses==[] and len(model.seen)==2
    asyncio.run(scenario())


def test_selected_child_can_query_history_and_root_can_read_its_saved_thread(monkeypatch,tmp_path):
    from naiyue_test_agent.tools import SubAgentSpec,create_subagent_tool
    async def scenario():
        root=StreamingToolModel(replies=[AIMessage(content='saved environment venv')])
        child=StreamingToolModel(replies=[])
        settings=AppSettings(subagents=(SubAgentSpec('tester','Read history','Query saved history',('history',),model_id='B'),))
        async with build(monkeypatch,tmp_path/'sessions.sqlite',root,[create_history_tool(),create_subagent_tool()],
                         settings=settings,models={'A':root,'B':child}) as app:
            old=await app.acreate_workspace_session('A',tmp_path)
            await app.arun(old,[('user','old environment')])
            child.replies.extend([call('history',{'action':'search','session_id':old,'query':'venv'},'inner'),AIMessage(content='child found environment')])
            root.replies.extend([call('subagent',{'agent':'tester','task':'find old environment'},'delegate'),AIMessage(content='root done')])
            owner=await app.acreate_workspace_session('A',tmp_path)
            await app.arun(owner,[('user','delegate')])
            tool=next(m for m in child.seen[-1] if isinstance(m,ToolMessage) and m.tool_call_id=='inner')
            assert tool.status=='success' and 'saved environment venv' in tool.content
            instance,=await app.aget_subagents(owner)
            root.replies.extend([call('history',{'action':'read','session_id':owner,'agent_id':instance['agent_id']},'read-child'),AIMessage(content='read saved child')])
            viewer=await app.acreate_workspace_session('A',tmp_path)
            result=await app.arun(viewer,[('user','read prior child')])
            assert feedback(result).status=='success' and 'child found environment' in feedback(result).content
            root.replies.extend([call('history',{'action':'read','session_id':old,'agent_id':instance['agent_id']},'wrong'),AIMessage(content='not owned')])
            result=await app.arun(viewer,[('user','wrong owner')])
            assert feedback(result).status=='error' and 'history_child_not_found' in feedback(result).content
            assert len(child.seen)==2
    asyncio.run(scenario())


def test_cold_target_is_read_without_restore_prepare_or_model_execution(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='cold environment venv')])
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_history_tool()]) as app:
            old=await app.acreate_workspace_session('A',tmp_path)
            await app.arun(old,[('user','save environment')])
        model=StreamingToolModel(replies=[call('history',{'action':'search','session_id':old,'query':'venv'}),AIMessage(content='found cold history')])
        async with build(monkeypatch,tmp_path/'sessions.sqlite',model,[create_history_tool()]) as app:
            assert not app._sessions.has_session(old)
            before=app._storage.get(old)
            async def forbidden(*args,**kwargs):raise AssertionError('History reads must not prepare target sessions')
            monkeypatch.setattr(app._persistence,'aprepare',forbidden)
            sid=await app.acreate_workspace_session('A',tmp_path)
            result=await app.arun(sid,[('user','find old environment')])
            assert feedback(result).status=='success' and 'cold environment venv' in feedback(result).content
            assert app._storage.get(old)==before
            assert not app._sessions.has_session(old) and len(model.seen)==2
    asyncio.run(scenario())


def test_forged_tool_arguments_never_reach_history_port():
    from types import SimpleNamespace
    from naiyue_test_agent.tools.permissions import PermissionMechanismError,execution_environment,permission_binding
    async def scenario():
        calls=[]
        async def forbidden(**kwargs):calls.append(kwargs)
        context=typed_context(identity=RunIdentity(session_id='source', agent_path='root'), capabilities=RunCapabilities(history=forbidden))
        canonical=HistoryInput(action='list_sessions').model_dump()
        call_row={'id':'call','name':'history','args':{'action':'list_sessions'},
                  'subject':{'args':canonical,'execution_environment':execution_environment()}}
        permit=permission_binding(context.scope.identity, 'batch', call_row)
        runtime=SimpleNamespace(context=replace(context, authorization=ToolAuthorization(permit=permit)),tool_call_id='call',
            state={'tool_execution':{'batch_id':'batch','calls':[call_row]}})
        with pytest.raises(PermissionMechanismError):
            await create_history_tool().tool.coroutine(runtime=runtime,action='read',session_id='other')
        assert calls==[]
    asyncio.run(scenario())
