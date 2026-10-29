"""Registered Host sessions resolve project position; old paths are audit facts."""
import asyncio
import json

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from test_project_admission import opened
from test_async_application import StreamingToolModel, call


async def moved_host(app, project, sid, destination, *, legacy_schema=False):
    await app.projects.close(project.project_id)
    if legacy_schema:
        import sqlite3
        from tests.legacy_store_schema import remove_project_local_schema
        with sqlite3.connect(project.database_path) as connection:
            remove_project_local_schema(connection)
            connection.execute('UPDATE naiyue_store_info SET version=18')
    project.source.path.rename(destination)
    await app.projects.relocate(project.project_id, expected_root=project.source.path, root=destination,
                               trusted=True, operation_id='move')
    host = await app.projects.open(project.project_id)
    await host.sessions.restore(sid)
    return host


def test_registered_session_keeps_binding_and_audit_path(monkeypatch, tmp_path):
    async def scenario():
        async with opened(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (_, project, host, sid):
            row = host.sessions._metadata.get(sid)
            assert row['project_id'] == project.project_id
            assert row['execution_binding'] == 'project_local'
            assert row['last_execution_path'] == str(tmp_path / 'a')
    asyncio.run(scenario())


@pytest.mark.parametrize('binding', [None, 'unknown'])
def test_untrusted_binding_is_not_inferred_from_a_saved_path(tmp_path, binding):
    from naiyue_test_agent.storage.execution_storage import root_from_row
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    row = dict(project_id='project', workspace_root=str(tmp_path), execution_root_kind='local',
               execution_binding=binding, last_execution_path=str(tmp_path))
    with pytest.raises(SessionPersistenceError, match='invalid_execution_identity'):
        root_from_row(row)


@pytest.mark.parametrize('legacy_schema', [False, True])
@pytest.mark.parametrize('clear_audit', [False, True])
def test_child_continuation_follows_original_project_binding(monkeypatch, tmp_path, legacy_schema, clear_audit):
    from naiyue_test_agent.tools import create_session_read_tool, create_subagent_tool, SubAgentSpec
    async def scenario():
        model = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'read'}, 'delegate'),
            call('read', {'path': 'file.txt'}, 'read-one'), AIMessage(content='child first'), AIMessage(content='parent first')])
        async with opened(monkeypatch, tmp_path, model,
                tools=[create_session_read_tool(), create_subagent_tool()],
                subagents=(SubAgentSpec('worker', 'Work', 'Report', ('read',)),)) as (app, project, host, sid):
            (tmp_path / 'a/file.txt').write_text('OLD_CONTENT')
            result = await host.runs.start(sid, [('user', 'delegate')])
            envelope = next(m for m in result['messages'] if isinstance(m, ToolMessage) and m.name == 'subagent')
            identity = json.loads(envelope.content)['agent_id']
            before = host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
            destination = tmp_path / 'moved'
            host = await moved_host(app, project, sid, destination, legacy_schema=legacy_schema)
            migrated = host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
            assert migrated['version'] == before['version'] and migrated['checkpoint_id'] == before['checkpoint_id']
            assert migrated['execution_binding'] == 'project_local' and migrated['last_execution_path'] == str(tmp_path / 'a')
            if clear_audit:
                import sqlite3
                with sqlite3.connect(project.database_path) as connection:
                    connection.execute('UPDATE naiyue_subagent_instances SET execution_root=NULL,last_execution_path=NULL WHERE agent_id=?', (identity,))
            (destination / 'file.txt').write_text('NEW_CONTENT')
            model.replies.extend([call('subagent', {'agent': 'worker', 'task': 'read again', 'agent_id': identity}, 'continue'),
                call('read', {'path': 'file.txt'}, 'read-two'), AIMessage(content='child second'), AIMessage(content='parent second')])
            result = await host.runs.start(sid, [('user', 'continue child')])
            assert result['messages'][-1].content == 'parent second'
            after = host.sessions._persistence.storage.subagents.get_subagent(sid, identity)
            assert after['child_thread_id'] == before['child_thread_id'] and after['version'] > before['version']
            assert after['execution_binding'] == 'project_local' and after['last_execution_path'] == str(destination)
            assert any(isinstance(m, ToolMessage) and 'NEW_CONTENT' in str(m.content) for m in model.seen[-2])
    asyncio.run(scenario())


def test_relocated_session_runs_at_new_position_with_one_saved_notice(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='before'), AIMessage(content='after'), AIMessage(content='again')])
        async with opened(monkeypatch, tmp_path, model) as (app, project, host, sid):
            await host.runs.start(sid, [('user', 'first')])
            destination = tmp_path / 'moved'
            host = await moved_host(app, project, sid, destination)
            status = await host.sessions.status(sid)
            assert status['workspace_root'] == str(destination) and status['execution_root_error'] is None
            assert host.sessions._metadata.get(sid)['last_execution_path'] == str(tmp_path / 'a')
            await host.runs.start(sid, [('user', 'continue')])
            systems = '\n'.join(str(m.content) for m in model.seen[-1] if m.type == 'system')
            assert '之前对话中的路径已失效' in systems
            assert str(tmp_path / 'a') in systems and str(destination) in systems
            assert host.sessions._metadata.get(sid)['last_execution_path'] == str(destination)
            await host.runs.start(sid, [('user', 'again')])
            assert not any('之前对话中的路径已失效' in str(m.content) for m in model.seen[-1] if m.type == 'system')
    asyncio.run(scenario())


def test_stale_approval_revision_refuses_same_path_and_can_be_stopped(monkeypatch, tmp_path):
    from naiyue_test_agent.application.errors import SessionApprovalExpiredError
    from naiyue_test_agent.tools import create_session_write_tool
    async def scenario():
        outside = tmp_path / 'outside.txt'
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'write', 'args': {'path': str(outside), 'content': 'no'}, 'id': 'write-one'}])])
        async with opened(monkeypatch, tmp_path, model, tools=[create_session_write_tool()]) as (app, project, host, sid):
            await host.runs.start(sid, [('user', 'ask')])
            assert host.runs.pending_tools(sid)
            await app.projects.rename(project.project_id, 'renamed', operation_id='rename')
            with pytest.raises(SessionApprovalExpiredError, match='approval_expired'):
                await host.runs.resume_approval(sid, ['approve'])
            assert not outside.exists() and host.runs.pending_tools(sid)
            pending = host.runs.pending_approvals(sid)[0]
            await host.runs.stop_approval(sid, approval_batch_id=pending['approval_batch_id'],
                                         origin_run_id=pending['origin_run_id'])
            assert not host.runs.pending_tools(sid) and not outside.exists()
    asyncio.run(scenario())


def test_expired_saved_approval_restores_for_cancellation_only(monkeypatch, tmp_path):
    from naiyue_test_agent.application.errors import SessionApprovalExpiredError
    from naiyue_test_agent.tools import create_session_write_tool
    async def scenario():
        outside = tmp_path / 'outside.txt'
        model = StreamingToolModel(replies=[call('write', {'path': str(outside), 'content': 'no'}, 'write-one')])
        tools = [create_session_write_tool()]
        async with opened(monkeypatch, tmp_path, model, tools=tools) as (app, project, host, sid):
            await host.runs.start(sid, [('user', 'ask')])
            await app.projects.rename(project.project_id, 'renamed', operation_id='rename')
        model = StreamingToolModel(replies=[])
        async with opened(monkeypatch, tmp_path, model, tools=tools) as (_, _, host, _):
            history = await host.history.session(sid)
            await host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
            with pytest.raises(SessionApprovalExpiredError):
                await host.runs.resume_approval(sid, ['approve'])
            batch = host.runs.pending_approvals(sid)[0]
            await host.runs.stop_approval(sid, approval_batch_id=batch['approval_batch_id'], origin_run_id=batch['origin_run_id'])
            assert not outside.exists() and model.seen == [] and not host.runs.pending_tools(sid)
    asyncio.run(scenario())


def test_schema18_registered_rows_migrate_to_project_local(monkeypatch, tmp_path):
    from tests.storage_backup_fixture import closed_store
    async def scenario():
        database, root, identity = closed_store(tmp_path / 'legacy', version=18)
        async with opened(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (app, _, _, _):
            result = await app.projects.import_legacy([database])
            assert result[0]['error'] is None and result[0]['project'].project_id == identity
            project = await app.projects.add_local(root, trusted=True, operation_id='trust-import')
            host = await app.projects.open(project.project_id)
            row = host.sessions._metadata.get('S')
            assert row['project_id'] == identity and row['execution_binding'] == 'project_local'
            assert row['last_execution_path'] == str(root)
            assert row['thread_id'] == 'T' and row['state'] == 'running'
    asyncio.run(scenario())


def test_stop_before_model_keeps_relocation_notice_for_next_run(monkeypatch, tmp_path):
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='before'), AIMessage(content='after')])
        async with opened(monkeypatch, tmp_path, model) as (app, project, host, sid):
            await host.runs.start(sid, [('user', 'before')])
            host = await moved_host(app, project, sid, tmp_path / 'moved')
            stopped = []
            def stop_once(event):
                if event.event_type == 'run_started' and not stopped:
                    stopped.append(True)
                    host.runs.stop(sid, run_id=event.run_id)
            host.observability.subscribe(stop_once)
            result = await host.runs.start(sid, [('user', 'cancel before model')])
            assert stopped == [True] and result['run_stopped'] and len(model.seen) == 1
            assert host.sessions._metadata.get(sid)['last_execution_path'] == str(tmp_path / 'a')
            await host.runs.start(sid, [('user', 'try again')])
            assert any('之前对话中的路径已失效' in str(m.content) for m in model.seen[-1] if m.type == 'system')
            assert host.sessions._metadata.get(sid)['last_execution_path'] == str(tmp_path / 'moved')
    asyncio.run(scenario())


def test_child_approval_revision_is_checked_before_accepting_decision(monkeypatch, tmp_path):
    from naiyue_test_agent.application.errors import SessionApprovalExpiredError
    from naiyue_test_agent.tools import create_session_write_tool, create_subagent_tool, SubAgentSpec
    async def scenario():
        outside = tmp_path / 'outside.txt'
        model = StreamingToolModel(replies=[call('subagent', {'agent': 'worker', 'task': 'write'}, 'child'),
            call('write', {'path': str(outside), 'content': 'no'}, 'write-child'),
            AIMessage(content='child rejected'), AIMessage(content='root done')])
        async with opened(monkeypatch, tmp_path, model,
                tools=[create_session_write_tool(), create_subagent_tool()],
                subagents=(SubAgentSpec('worker', 'Work', 'Report', ('write',)),)) as (app, project, host, sid):
            running = asyncio.create_task(host.runs.start(sid, [('user', 'delegate')]))
            async def pending():
                while True:
                    batches = host.runs.pending_subagents(sid)
                    if batches:
                        return batches[0]
                    await asyncio.sleep(0)
            try:
                batch = await asyncio.wait_for(pending(), 5)
                await app.projects.rename(project.project_id, 'renamed', operation_id='rename')
                args = dict(approval_batch_id=batch['approval_batch_id'], origin_run_id=batch['origin_run_id'])
                approval_id = batch['items'][0]['approval_id']
                with pytest.raises(SessionApprovalExpiredError):
                    await host.runs.submit_subagent_approval(sid, decisions=[{'approval_id': approval_id, 'decision': 'approve'}], **args)
                assert not outside.exists() and host.runs.pending_subagents(sid)
                await host.runs.submit_subagent_approval(sid, decisions=[{'approval_id': approval_id, 'decision': 'reject'}], **args)
                assert (await running)['messages'][-1].content == 'root done'
                assert not outside.exists() and not host.runs.pending_subagents(sid)
            finally:
                if not running.done():
                    host.runs.stop(sid)
                await asyncio.gather(running, return_exceptions=True)
    asyncio.run(scenario())


def test_project_binding_executes_without_any_saved_execution_path(monkeypatch, tmp_path):
    import sqlite3
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='bound position')])
        async with opened(monkeypatch, tmp_path, model) as (app, project, host, sid):
            await app.projects.close(project.project_id)
            with sqlite3.connect(project.database_path) as connection:
                connection.execute('UPDATE naiyue_sessions SET workspace_root=NULL,last_execution_path=NULL WHERE session_id=?', (sid,))
            host = await app.projects.open(project.project_id)
            await host.sessions.restore(sid)
            status = await host.sessions.status(sid)
            assert status['workspace_root'] == str(tmp_path / 'a') and status['can_run']
            assert status['last_execution_path'] is None
            assert (await host.runs.start(sid, [('user', 'run by binding')]))['messages'][-1].content == 'bound position'
            assert host.sessions._metadata.get(sid)['last_execution_path'] == str(tmp_path / 'a')
    asyncio.run(scenario())


def test_cold_history_and_context_preview_resolve_current_project_position(monkeypatch, tmp_path):
    from naiyue_test_agent.application.sessions import session_context
    async def scenario():
        model = StreamingToolModel(replies=[AIMessage(content='before')])
        async with opened(monkeypatch, tmp_path, model) as (app, project, host, sid):
            await host.runs.start(sid, [('user', 'before')])
            await app.projects.close(project.project_id)
            destination = tmp_path / 'moved'
            project.source.path.rename(destination)
            await app.projects.relocate(project.project_id, expected_root=project.source.path, root=destination,
                                       trusted=True, operation_id='move-cold')
            host = await app.projects.open(project.project_id)
            assert not host.sessions.is_loaded(sid)
            observed = []
            original = session_context.prepare_resources
            async def resources(root, **options):
                observed.append(root.environment.root)
                return await original(root, **options)
            monkeypatch.setattr(session_context, 'prepare_resources', resources)
            history = await host.history.session(sid)
            assert history['workspace_root'] == str(destination)
            await host.history.context(sid)
            assert observed == [str(destination)]
            assert not host.sessions.is_loaded(sid) and len(model.seen) == 1
            assert host.sessions._metadata.get(sid)['last_execution_path'] == str(project.source.path)
    asyncio.run(scenario())


def test_first_relocated_run_keeps_project_memory_injection(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.tools.memory import create_memory_tool
    from test_memory_readonly import seed
    async def scenario():
        preference='Preserve project memory after an explicit relocation'
        model=StreamingToolModel(replies=[AIMessage(content='before'),AIMessage(content='after')])
        async with opened(monkeypatch,tmp_path,model,tools=[create_memory_tool()]) as (app,project,host,sid):
            await host.runs.start(sid,[('user','before')])
            store=host.sessions._persistence.storage
            seed(store.database.connection,1,workspace_id=project.project_id,kind='user',body=preference)
            host=await moved_host(app,project,sid,tmp_path/'moved')
            caplog.clear()
            await host.runs.start(sid,[('user','after')])
            assert preference in ''.join(str(m.content) for m in model.seen[-1] if m.type=='system')
            assert 'memory_context_prepare_failed' not in caplog.text
            assert len(model.seen)==2
    asyncio.run(scenario())
