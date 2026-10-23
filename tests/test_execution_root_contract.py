"""Execution loss blocks new work without taking persisted conversations away."""

from dataclasses import replace
from runtime_context_helpers import ExecutionRoot, RunIdentity, ToolAuthorization, typed_context
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import SubAgentSpec, create_subagent_tool
from naiyue_test_agent.tools.permissions import FilePermission
from naiyue_test_agent.tools.processes import ProcessRequest
from naiyue_test_agent.tools.settings import BashSettings
from naiyue_test_agent.workspaces import prepare_workspace
from test_async_application import StreamingToolModel, call
from test_subagent_execution import ChildModel


@asynccontextmanager
async def opened(monkeypatch, folder, model, *, location=None, child=None):
    models = {'A': model, **({'B': child} if child is not None else {})}
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', models))
    roles = (SubAgentSpec('worker', 'Inspect', 'Report', (), model_id='B'),) if child else ()
    settings = AppSettings.model_validate({'storage': {'data_dir': folder / 'data'},
        'memory': {'auto_enabled': False}, 'permissions': {'mode': 'BypassApprovals'}, 'subagents': roles})
    binding = {'workspace_id': location.workspace_id, 'root_path': location.root} if location else None
    async with open_async_persistent_application('unused', database_path=folder / 'state.sqlite',
            tools=[create_subagent_tool()] if child else [], system_prompt='', response_format=None,
            settings=settings, workspace_binding=binding) as app:
        yield app


def project(folder):
    root = folder / 'execution'
    root.mkdir()
    return root, prepare_workspace(root, data_dir=folder / 'data', database_path=folder / 'state.sqlite')


def test_missing_execution_root_keeps_history_but_blocks_new_run(monkeypatch, tmp_path):
    async def scenario():
        root, location = project(tmp_path)
        model = StreamingToolModel(replies=[AIMessage(content='saved'), AIMessage(content='must not run')])
        async with opened(monkeypatch, tmp_path, model, location=location) as app:
            sid = await app.acreate_workspace_session('A', root)
            await app.arun(sid, [('user', 'original')])
        root.rename(tmp_path / 'removed')
        async with opened(monkeypatch, tmp_path, model) as app:
            history = await app.aget_session_history(sid)
            assert any(m.content == 'saved' for m in history['messages'])
            await app.arestore_session(sid)
            status = await app.aget_session_status(sid)
            assert not status['can_run']
            assert status['execution_root_error'] == {'code': 'execution_root_unavailable', 'reason': 'missing'}
            with pytest.raises(RuntimeError, match='execution_root_unavailable.*missing'):
                await app.arun(sid, [('user', 'new execution')])
            assert len(model.seen) == 1
            assert not app._sessions.require(sid).is_busy()
    asyncio.run(scenario())


def test_missing_child_execution_root_rejects_continuation_without_rebinding(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.application.subagents import subagent_records
        root, location = project(tmp_path)
        model = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'first'}), AIMessage(content='done')])
        child = ChildModel(replies=[AIMessage(content='child saved'), AIMessage(content='must not run')])
        async with opened(monkeypatch, tmp_path, model, location=location, child=child) as app:
            sid = await app.acreate_workspace_session('A', root)
            result = await app.arun(sid, [('user', 'delegate')])
            report = next(m for m in result['messages'] if isinstance(m, ToolMessage))
            identity = json.loads(report.content)['agent_id']
            before = app._storage.get_subagent(sid, identity)
            original = subagent_records.admit
            async def disappear(owner, graph, instance, spec):
                root.rename(tmp_path / 'removed')
                return await original(owner, graph, instance, spec)
            monkeypatch.setattr(subagent_records, 'admit', disappear)
            model.replies.extend([call('subagent', {'agent': 'worker', 'task': 'continue', 'agent_id': identity}, 'again'),
                                  AIMessage(content='continuation rejected')])
            result = await app.arun(sid, [('user', 'continue')])
            errors = [m for m in result['messages'] if isinstance(m, ToolMessage) and m.tool_call_id == 'again']
            assert errors and 'execution_root_unavailable' in str(errors[0].content) and 'missing' in str(errors[0].content)
            assert len(child.seen) == 1
            assert app._storage.get_subagent(sid, identity) == before
    asyncio.run(scenario())


def test_missing_execution_root_rejects_background_submit_without_spawn(monkeypatch, tmp_path):
    async def scenario():
        root, location = project(tmp_path)
        model = StreamingToolModel(replies=[])
        class Runner:
            calls = 0
            async def run(self, *_args, **_kwargs):
                self.calls += 1
                raise AssertionError('An unavailable cwd must not create a process')
        runner = Runner()
        async with opened(monkeypatch, tmp_path, model, location=location) as app:
            sid = await app.acreate_workspace_session('A', root)
            request = ProcessRequest(['unused'], root, {})
            root.rename(tmp_path / 'removed')
            with pytest.raises(ToolException, match='execution_root_unavailable.*missing'):
                await app._sessions.background_tasks.submit(session_id=sid, workspace_root=root,
                    archive_root=app._sessions.require(sid)._archive_root, run_id='R', tool_call_id='C',
                    agent_path='root', command='unused', name='bash', request=request,
                    settings=BashSettings(), runner=runner, control=None)
            assert runner.calls == 0
            assert (await app.alist_background_tasks(sid))['items'] == []
    asyncio.run(scenario())


@pytest.mark.parametrize('operation', ['read', 'write', 'list', 'find', 'search'])
def test_file_permission_rejects_missing_root_even_for_existing_external_target(tmp_path, operation):
    root = tmp_path / 'execution'
    root.mkdir()
    external = tmp_path / 'external'
    external.mkdir() if operation in ('list', 'find', 'search') else external.write_text('sentinel')
    root.rmdir()
    with pytest.raises(ToolException, match='execution_root_unavailable.*missing'):
        FilePermission(operation=operation).prepare({'path': str(external)}, typed_context(identity=RunIdentity(execution_root=ExecutionRoot(root) if root is not None else None, session_id=None)))
    assert external.is_dir() or external.read_text() == 'sentinel'


def test_background_submit_checks_project_identity_before_spawn(monkeypatch, tmp_path):
    async def scenario():
        from uuid import uuid4
        from naiyue_test_agent.execution_roots import ExecutionRoot
        root, location = project(tmp_path)
        async with opened(monkeypatch, tmp_path, StreamingToolModel(replies=[]), location=location) as app:
            sid = await app.acreate_workspace_session('A', root)
            with pytest.raises(ToolException, match='execution_root_unavailable.*wrong_project'):
                await app._sessions.background_tasks.submit(session_id=sid, workspace_root=root,
                    execution_root=ExecutionRoot(root), project_id=str(uuid4()),
                    archive_root=app._sessions.require(sid)._archive_root, run_id='R', tool_call_id='C',
                    agent_path='root', command='unused', name='bash', request=ProcessRequest(['unused'], root, {}),
                    settings=BashSettings(), runner=None, control=None)
            assert (await app.alist_background_tasks(sid))['items'] == []
    asyncio.run(scenario())


def test_missing_execution_root_still_allows_stopping_pending_approval(monkeypatch, tmp_path):
    async def scenario():
        from naiyue_test_agent.application.async_application import build_async_local_application
        from naiyue_test_agent.tools import create_session_read_tool
        root = tmp_path / 'execution'
        root.mkdir()
        target = tmp_path / 'external.txt'
        target.write_text('must not read')
        model = StreamingToolModel(replies=[call('read', {'path': str(target)})])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_async_local_application('unused', tools=[create_session_read_tool()],
            system_prompt='', response_format=None)
        try:
            events = []
            app.subscribe_events(events.append)
            sid = await app.acreate_workspace_session('A', root)
            await app.arun(sid, [('user', 'request external file')])
            batch, = app.pending_approval_batches(sid)
            root.rename(tmp_path / 'removed')
            stopped = await app.astop_tool_approval(sid, approval_batch_id=batch['approval_batch_id'],
                origin_run_id=batch['origin_run_id'])
            assert stopped['run_stopped'] is True
            feedback, = [m for m in stopped['messages'] if isinstance(m, ToolMessage)]
            assert feedback.additional_kwargs['naiyue_not_executed'] == 'user_stop'
            assert app.pending_approval_batches(sid) == [] and len(model.seen) == 1
            assert [e.data.status for e in events if e.event_type == 'run_finished'] == ['interrupted', 'stopped']
        finally:
            await app.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('reason', ['not_directory', 'wrong_project', 'not_registered'])
def test_restored_execution_identity_reports_reason_without_new_model_call(monkeypatch, tmp_path, reason):
    async def scenario():
        root, location = project(tmp_path)
        model = StreamingToolModel(replies=[AIMessage(content='saved')])
        async with opened(monkeypatch, tmp_path, model, location=location) as app:
            sid = await app.acreate_workspace_session('A', root)
            await app.arun(sid, [('user', 'save')])
            if reason == 'not_registered':
                with app._storage.connection:
                    app._storage.connection.execute(
                        "UPDATE naiyue_sessions SET execution_root_kind='worktree' WHERE session_id=?", (sid,))
        if reason != 'not_registered':
            root.rename(tmp_path / 'original-directory')
            if reason == 'not_directory':
                root.write_text('not a directory')
            else:
                root.mkdir()
                replacement = prepare_workspace(root, data_dir=tmp_path / 'data')
                assert replacement.workspace_id != location.workspace_id
        async with opened(monkeypatch, tmp_path, model) as app:
            await app.arestore_session(sid)
            status = await app.aget_session_status(sid)
            assert status['execution_root_error'] == {'code': 'execution_root_unavailable', 'reason': reason}
            assert not status['can_run']
            with pytest.raises(RuntimeError, match=f'execution_root_unavailable.*{reason}'):
                await app.arun(sid, [('user', 'must not execute')])
            assert len(model.seen) == 1 and not app._sessions.require(sid).is_busy()
    asyncio.run(scenario())


def test_execution_permit_cannot_be_consumed_in_another_directory(tmp_path):
    from types import SimpleNamespace
    from naiyue_test_agent.tools.permissions import PermissionMechanismError, execution_environment, require_tool_permit
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    permit = {'session_id': 'S', 'agent_path': 'root', 'call_id': 'C',
        'subject': {'workspace': str(first), 'execution_environment': execution_environment()}}
    runtime = SimpleNamespace(tool_call_id='C', context=typed_context(identity=RunIdentity(session_id='S', execution_root=ExecutionRoot(second) if second is not None else None), authorization=ToolAuthorization(permit=permit)))
    with pytest.raises(PermissionMechanismError, match='execution directory differs'):
        require_tool_permit(runtime)
    runtime.context = replace(runtime.context, scope=replace(runtime.context.scope, identity=replace(runtime.context.scope.identity, execution_root=ExecutionRoot(first) if first is not None else None)))
    assert require_tool_permit(runtime) is permit


def test_file_busy_keys_still_use_absolute_targets_across_execution_directories(tmp_path):
    from naiyue_test_agent.tools.write import _reserve_target
    first, second = tmp_path / 'first' / 'same.txt', tmp_path / 'second' / 'same.txt'
    with _reserve_target(first):
        with _reserve_target(second):
            with pytest.raises(ToolException, match='busy'):
                with _reserve_target(first):
                    pytest.fail('The same actual target must remain mutually exclusive')
    with _reserve_target(first):
        pass
