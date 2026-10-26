"""Cross-domain cases found by independent architecture and security review."""
import asyncio

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.tools.output import ShellOutputArchive
from naiyue_test_agent.tools.settings import BashSettings
from test_async_application import StreamingToolModel, call
from test_multi_project_runtime import multi_app, projects


def test_remove_open_project_keeps_registration_active(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='ok')])) as (app, _):
            (pa, a), _ = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            with pytest.raises(ProjectError, match='project_in_use'):
                await app.projects.remove(pa.project_id, operation_id='remove')
            assert (await app.projects.get(pa.project_id)).lifecycle == 'active'
            assert all(item.operation_id != 'remove' for item in await app.projects.operations())
            await a.runs.start(sid, [('user', 'still usable')])
    asyncio.run(scenario())


def test_relocate_requires_project_close(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (pa, _), _ = await projects(app, tmp_path)
            moved = tmp_path / 'moved'
            moved.mkdir()
            with pytest.raises(ProjectError, match='project_in_use'):
                await app.projects.relocate(pa.project_id, expected_root=tmp_path / 'a', root=moved,
                    trusted=True, operation_id='move')
            assert (await app.projects.get(pa.project_id)).source.path == tmp_path / 'a'
    asyncio.run(scenario())


def test_reopened_registered_project_restores_approval_with_bound_root(monkeypatch, tmp_path):
    async def scenario():
        outside = tmp_path / 'outside.txt'
        outside.write_text('approved read')
        tools = [create_session_read_tool()]
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[
                call('read', {'path': str(outside)})]), tools=tools) as (app, _):
            (pa, a), _ = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            result = await a.runs.start(sid, [('user', 'read outside')])
            assert result['approval_requests']
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[AIMessage(content='done')]),
                tools=tools) as (app, _):
            a = await app.projects.open(pa.project_id)
            history = await a.history.session(sid)
            assert history['recovery']['actions'] == ['restore_approval', 'continue']
            await a.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
            assert a.runs.pending_tools(sid)
            result = await a.runs.resume_approval(sid, ['approve'])
            output = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            assert output.status == 'success' and 'approved read' in output.content
            assert not (tmp_path / 'a/.naiyue').exists()
    asyncio.run(scenario())


def test_output_clear_plan_cannot_cross_project_host(monkeypatch, tmp_path):
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (_, a), (_, b) = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            archive = ShellOutputArchive(a.sessions._sessions.session_archive_root(sid), BashSettings())
            archive.record(b'owned output', activate=True)
            archive.finish()
            plan = await a.tasks.preview_outputs(tmp_path / 'a')
            assert plan.file_count
            with pytest.raises(ValueError, match='Archive scope'):
                await b.tasks.clear_outputs(plan)
            assert (await a.tasks.preview_outputs(tmp_path / 'a')).file_count == plan.file_count
            actor, run = a.sessions._sessions._admit(sid, 'busy', 'message')
            try:
                with pytest.raises(ValueError, match='active run'):
                    await a.tasks.clear_outputs(plan)
            finally:
                actor.controls.close_control_window(run)
                actor.lifecycle.release_run(run)
    asyncio.run(scenario())


def test_registration_read_failure_is_unavailable_without_losing_history(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    from naiyue_test_agent.execution_roots import ExecutionRootUnavailable
    async def scenario():
        async with multi_app(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _):
            (_, a), _ = await projects(app, tmp_path)
            sid = await a.sessions.create('A')
            def fail(_):
                raise SessionPersistenceError('PRIVATE_REGISTRY_IO')
            monkeypatch.setattr(app.projects._service.registry, 'execution_facts', fail)
            status = await a.sessions.status(sid)
            assert status['execution_root_error'] == {'code': 'execution_root_unavailable', 'reason': 'unreadable'}
            assert status['can_run'] is False
            assert await a.history.messages(sid) == []
            with pytest.raises(ExecutionRootUnavailable, match='unreadable'):
                await a.runs.start(sid, [('user', 'blocked')])
        assert 'execution_registration_check_failed' in caplog.text and 'PRIVATE_REGISTRY_IO' not in caplog.text
    asyncio.run(scenario())
