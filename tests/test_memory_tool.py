"""Real create_agent calls, configuration and permission boundaries for memory."""

import asyncio
from contextlib import asynccontextmanager
import json
import sqlite3
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.tools import create_builtin_tools, create_subagent_tool, SubAgentSpec
from naiyue_test_agent.tools.memory import MemoryInput, create_memory_tool
from test_async_application import StreamingToolModel, call, make_app
from test_memory_readonly import seed


@asynccontextmanager
async def opened(monkeypatch, root, model, *, settings=None, tools=None, identity=None, models=None):
    settings = settings or AppSettings.model_validate({'storage':{'data_dir':str(root / 'data')}})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _:ModelCatalog('A', models or {'A':model}))
    from naiyue_test_agent.workspaces import prepare_workspace
    if identity is not None and not (root / '.naiyue/workspace.json').exists():
        (root / '.naiyue').mkdir(exist_ok=True)
        (root / '.naiyue/workspace.json').write_text(json.dumps({'version': 1, 'workspace_id': identity}))
    location = prepare_workspace(root, data_dir=root / 'data', database_path=root / 'sessions.sqlite')
    async with open_async_persistent_application('unused', database_path=root / 'sessions.sqlite',
            tools=tools or [create_memory_tool()], system_prompt='', response_format=None, settings=settings,
            workspace_binding={'workspace_id':identity or location.workspace_id, 'root_path':root}) as app:
        yield app


def feedback(result):
    return [m for m in result['messages'] if isinstance(m, ToolMessage) and m.name == 'memory']


@pytest.mark.parametrize('args', [dict(action='search'), dict(action='read'),
    dict(action='read', memory_id='a' * 32), dict(action='list', query='extra'),
    dict(action='search', query=' '), dict(action='list', workspace_id='other'),
    dict(action='read', memory_id='a' * 32, scope='personal', version=2**100),
    dict(action='search', query='x', expansions=['x' * 2001])])
def test_operation_schema_cannot_select_arbitrary_authority(args):
    with pytest.raises(ValidationError):MemoryInput(**args)


def test_settings_and_builtin_are_real_and_invalid_memory_is_disabled(tmp_path, caplog):
    path = tmp_path / 'settings.toml'
    path.write_text('[memory]\ndefault_limit=3\nmax_limit=7\nfield_weights=[1,1,1]\n')
    settings = load_settings(path)
    assert settings.memory.default_limit == 3 and settings.memory.max_limit == 7
    assert settings.memory.field_weights == (1.0, 1.0, 1.0)
    spec = next(s for s in create_builtin_tools(settings.tools) if s.tool.name == 'memory')
    assert spec.side_effects == 'may_mutate' and spec.execution_mode == 'parallel'
    assert spec.permissions.default == 'allow' and 'runtime' not in spec.input_schema.model_fields
    path.write_text('[memory]\nmax_limit=0\nprivate="must-not-log"\n')
    assert not load_settings(path).memory.enabled
    assert 'memory_settings_invalid' in caplog.text and 'must-not-log' not in caplog.text


def test_formal_list_search_read_then_reopen_without_internal_model_call(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        identity = str(uuid4())
        async with opened(monkeypatch, tmp_path, model, identity=identity) as app:
            item = seed(app.sessions._persistence.storage.database.connection, 1, workspace_id=identity, body='使用.venv运行pytest')
            model.replies.extend([call('memory', {'action':'list','scope':'workspace'}, 'list'),
                call('memory', {'action':'search','query':'.venv','scope':'workspace'}, 'search'),
                call('memory', {'action':'read','scope':'workspace','memory_id':item,'version':1}, 'read'),
                AIMessage(content='found environment')])
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user','查记忆里的测试环境')])
            messages = feedback(result)
            assert [m.tool_call_id for m in messages] == ['list', 'search', 'read']
            assert all(m.status == 'success' for m in messages)
            assert json.loads(messages[1].content)['items'][0]['memory_id'] == item
            assert '使用.venv运行pytest' in messages[-1].content
            assert len(model.seen) == 4 and not app.sessions._sessions.require(sid).is_busy()
        reopened = StreamingToolModel(replies=[call('memory', {'action':'search','query':'.venv'}, 'cold'),AIMessage(content='saved memory')])
        async with opened(monkeypatch, tmp_path, reopened, identity=identity) as app:
            assert not app.sessions.is_loaded(sid)
            new = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(new, [('user','读取已保存记忆')])
            assert feedback(result)[0].status == 'success' and item in feedback(result)[0].content
            assert len(reopened.seen) == 2
    asyncio.run(scenario())


def test_real_sqlite_query_failure_is_paired_and_agent_continues(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.application.memory import memory_queries
    async def scenario():
        model = StreamingToolModel(replies=[call('memory', {'action':'search','query':'venv'}, 'bad'),AIMessage(content='try current files')])
        async with opened(monkeypatch, tmp_path, model) as app:
            def failed(*args, **kwargs):raise sqlite3.OperationalError('PRIVATE-sql-provider-body')
            monkeypatch.setattr(memory_queries, 'rank', failed)
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user','query')])
            message, = feedback(result)
            assert message.status == 'error' and message.tool_call_id == 'bad'
            assert 'memory_read_failed' in message.content
            assert result['messages'][-1].content == 'try current files' and len(model.seen) == 2
            assert 'PRIVATE-sql-provider-body' not in message.content + caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('blocked', ['inactive', 'deny', 'disabled'])
def test_blocked_memory_never_opens_personal_store(monkeypatch, tmp_path, blocked):
    async def scenario():
        settings = AppSettings.model_validate({'storage':{'data_dir':str(tmp_path / 'data')},
            'memory':{'enabled':blocked != 'disabled'},
            'permissions':{'deny':[{'tool':'memory'}]} if blocked == 'deny' else {}})
        model = StreamingToolModel(replies=[call('memory', {'action':'list'}), AIMessage(content='blocked')])
        async with opened(monkeypatch, tmp_path, model, settings=settings) as app:
            sid = await app.sessions.create_workspace('A', tmp_path)
            if blocked == 'inactive':await app.sessions.select_tools(sid, [])
            result = await app.runs.start(sid, [('user','query')])
            assert feedback(result)[0].status == 'error' and len(model.seen) == 2
            assert not (tmp_path / 'data/personal/memory.sqlite').exists()
    asyncio.run(scenario())


def test_selected_child_gets_its_own_memory_permit(monkeypatch, tmp_path):
    async def scenario():
        root = StreamingToolModel(replies=[call('subagent', {'agent':'tester','task':'read memory'}),AIMessage(content='root done')])
        child = StreamingToolModel(replies=[call('memory', {'action':'search','scope':'workspace','query':'venv'}, 'inner'),AIMessage(content='child done')])
        settings = AppSettings.model_validate({'storage':{'data_dir':str(tmp_path / 'data')}}).model_copy(update={
            'subagents':(SubAgentSpec('tester','Read memory','Read saved environment.',('memory',),model_id='B'),)})
        async with opened(monkeypatch, tmp_path, root, settings=settings,
                tools=[create_memory_tool(),create_subagent_tool()],models={'A':root,'B':child}) as app:
            identity = app.sessions._persistence.storage.sessions.get_workspace_binding()['workspace_id']
            seed(app.sessions._persistence.storage.database.connection, 1, workspace_id=identity)
            sid = await app.sessions.create_workspace('A', tmp_path)
            await app.runs.start(sid, [('user','delegate')])
            message = next(m for m in child.seen[-1] if isinstance(m,ToolMessage) and m.tool_call_id == 'inner')
            assert message.status == 'success' and 'venv' in message.content and len(child.seen) == 2
    asyncio.run(scenario())


def test_nonpersistent_runtime_is_explicitly_unavailable(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('memory', {'action':'list'}),AIMessage(content='unavailable')])
        app = await make_app(monkeypatch, model, [create_memory_tool()])
        try:
            sid = await app.sessions.create_workspace('A', tmp_path)
            result = await app.runs.start(sid, [('user','query')])
            assert feedback(result)[0].status == 'error' and 'memory_unavailable' in feedback(result)[0].content
        finally:await app.aclose()
    asyncio.run(scenario())


def test_memory_cleanup_failure_still_releases_project_store(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[])
        expected = RuntimeError('memory-close-failure')
        closed = []
        with pytest.raises(RuntimeError) as caught:
            async with opened(monkeypatch, tmp_path, model) as app:
                original = app.sessions._persistence.storage.close
                def close_store():
                    closed.append(True)
                    original()
                def fail():raise expected
                monkeypatch.setattr(app.sessions._persistence.storage, 'close', close_store)
                monkeypatch.setattr(app.sessions._persistence._memory, 'close', fail)
        assert caught.value is expected and closed == [True]
    asyncio.run(scenario())
