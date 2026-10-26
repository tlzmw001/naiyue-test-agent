"""Registered roots reach real tools, child graphs and process custody without markers."""

from naiyue_test_agent.execution.host_files import HostFileSystem
import asyncio
import json
from dataclasses import replace

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException
import pytest

from naiyue_test_agent.tools import create_session_read_tool, create_subagent_tool, SubAgentSpec
from naiyue_test_agent.tools.processes import HostProcessRunner
from naiyue_test_agent.tools.settings import BashSettings
from naiyue_test_agent.contracts.projects import ProjectError
from test_async_application import StreamingToolModel, call
from test_background_tasks import wait_status
from test_process_lifecycle import request
from test_multi_project_runtime import multi_app, projects


def test_registered_root_read_and_child_graphs_use_their_project_saver(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('read', {'path': 'file.txt'}), AIMessage(content='read'),
            call('subagent', {'agent': 'worker', 'task': 'first'}, 'first'), AIMessage(content='child one'),
            AIMessage(content='parent one'), call('subagent', {'agent': 'worker', 'task': 'next'}, 'other'),
            AIMessage(content='child two'), AIMessage(content='parent two')])
        role = SubAgentSpec('worker', 'Work', 'Report', ())
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_session_read_tool(), create_subagent_tool()],
                subagents=(role,)) as (app, _):
            (_, a), (_, b) = await projects(app, tmp_path)
            (tmp_path / 'a/file.txt').write_text('only A')
            sa, sb = await a.sessions.create('A'), await b.sessions.create('A')
            result = await a.runs.start(sa, [('user', 'read')])
            message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert message.status == 'success' and 'only A' in message.content
            await a.runs.start(sa, [('user', 'delegate')])
            await b.runs.start(sb, [('user', 'delegate')])
            for host in (a, b):
                child = host.sessions._sessions._subagents['worker']['agent']
                assert child.checkpointer.backend is host.runs._agent.checkpointer
            assert a.sessions._sessions._subagents['worker']['agent'] is not b.sessions._sessions._subagents['worker']['agent']
            assert len(await a.history.subagents(sa)) == len(await b.history.subagents(sb)) == 1
    asyncio.run(scenario())


def test_child_continuation_checks_saved_root_after_parent_admission(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'first'}, 'first'),
            AIMessage(content='child'), AIMessage(content='done')])
        role = SubAgentSpec('worker', 'Work', 'Report', ())
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_subagent_tool()], subagents=(role,)) as (app, _):
            (_, a), _ = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            result = await a.runs.start(sid, [('user', 'first')])
            child = json.loads(next(m.content for m in result['messages'] if isinstance(m, ToolMessage)))
            before = a.sessions._persistence.storage.subagents.get_subagent(sid, child['agent_id'])
            model.replies.extend([call('subagent', {'agent': 'worker', 'task': 'continue',
                'agent_id': child['agent_id']}, 'second'), AIMessage(content='unavailable')])
            entered, release = asyncio.Event(), asyncio.Event()
            original = model._next
            async def gate(messages):
                entered.set()
                await release.wait()
                return await original(messages)
            # The gate is per-model behavior, not a production timing hook.
            monkeypatch.setattr(StreamingToolModel, '_next', lambda self, messages: gate(messages))
            running = asyncio.create_task(a.runs.start(sid, [('user', 'next')]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                (tmp_path / 'a').rename(tmp_path / 'gone')
            finally:
                release.set()
            result = await running
            feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)][-1]
            assert 'execution_root_unavailable' in feedback.content and 'missing' in feedback.content
            assert a.sessions._persistence.storage.subagents.get_subagent(sid, child['agent_id']) == before
            assert len(model.seen) == 5  # No second child model call.
    asyncio.run(scenario())


def test_running_process_survives_root_loss_but_new_submit_fails(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (pa, a), _ = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            service = a.sessions._sessions
            root = tmp_path / 'a'
            frozen = service.require(sid).snapshot_for_context()['execution_root']
            manager = service.background_tasks
            script = "import time; print('ready',flush=True); time.sleep(60)"
            environment = HostFileSystem(frozen, pa.project_id).environment
            options = dict(session_id=sid, workspace_root=root, execution_root=frozen, project_id=pa.project_id, environment=environment,
                archive_root=service.session_archive_root(sid), run_id='run', tool_call_id='one', agent_path='root',
                command=script, name='bash', request=replace(request(root, script), merge_output=True, execution_environment=environment.public()),
                settings=BashSettings(), runner=HostProcessRunner(), control=None)
            row = await manager.submit(**options)
            await wait_status(manager, row['task_id'], lambda value: 'ready' in value['output'])
            with pytest.raises(ProjectError, match='project_in_use'):
                await app.projects.close(pa.project_id)
            root.rename(tmp_path / 'gone')
            with pytest.raises(ToolException, match='execution_root_unavailable.*missing'):
                await manager.submit(**(options | {'tool_call_id': 'two'}))
            page = await a.tasks.list(sid)
            assert len(page['items']) == 1 and page['active_count'] == 1
            output = await a.tasks.output(row['task_id'], sid)
            assert 'ready' in output['output'] and output['archive_paths']
            assert service.session_archive_root(sid).is_dir()
            stopped = await a.tasks.stop(row['task_id'], sid)
            assert stopped['cleanup_confirmed'] and stopped['termination'] == 'user_stop'
    asyncio.run(scenario())


def test_registered_store_rejects_foreign_child_identity_before_open(monkeypatch, tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    async def scenario():
        model = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'first'}),
            AIMessage(content='child'), AIMessage(content='done')])
        async with multi_app(monkeypatch, tmp_path, model, tools=[create_subagent_tool()],
                subagents=(SubAgentSpec('worker', 'Work', 'Report', ()),)) as (app, _):
            (pa, a), (pb, _) = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            await a.runs.start(sid, [('user', 'delegate')])
            await app.projects.close(pa.project_id)
            with ProjectStore(pa.database_path) as store:
                with store.database.transaction() as connection:
                    connection.execute('UPDATE naiyue_subagent_instances SET project_id=?', (pb.project_id,))
            before = pa.database_path.read_bytes()
            with pytest.raises(ProjectError, match='project_identity_conflict'):
                await app.projects.open(pa.project_id)
            assert pa.database_path.read_bytes() == before
    asyncio.run(scenario())
