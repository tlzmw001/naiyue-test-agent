"""Cold approval recovery keeps the official interrupt and executes only on decision."""

from runtime_context_helpers import ExecutionRoot, RunIdentity, typed_context
from native_application_helpers import async_test

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.storage.session_storage import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_session_read_tool, create_session_write_tool
from naiyue_test_agent.settings import AppSettings
from test_application_run_events import ToolModel
from test_async_application import StreamingToolModel
from test_session_history import approval_snapshot, database_facts
from test_persistent_sessions import build


@pytest.mark.parametrize('streaming_model', [False, True])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
@async_test
async def test_reopened_mixed_sequential_batch_waits_then_runs_once(monkeypatch, tmp_path, streaming_model, decision):
    root = tmp_path / 'work'
    root.mkdir()
    (root / 'note').write_text('saved input')
    target = tmp_path / 'outside.txt'
    path = tmp_path / 'state.sqlite'
    tools = [create_session_read_tool(), create_session_write_tool()]
    first = AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': 'note'}, 'id': 'read-call'},
        {'name': 'write', 'args': {'path': str(target), 'content': 'approved result'}, 'id': 'write-call'}])

    @asynccontextmanager
    async def opened(replies):
        model = StreamingToolModel(replies=replies) if streaming_model else ToolModel(responses=replies)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        options = dict(tools=tools, system_prompt='', response_format=None)
        async with open_async_persistent_application('unused', database_path=path, **options) as app:
            yield app, model

    async def scenario():
        async with opened([first]) as (app, _):
            sid = await app.acreate_workspace_session('A', root)
            await app.arun(sid, [('user', 'read note and write the external result')])
            row = app._storage.get(sid)
            config = {'configurable': {'thread_id': row['thread_id']}}
            original = await app._agent.aget_state(config)
        assert not target.exists()
        async with opened([AIMessage(content='done')]) as (app, model):
            events = []
            app.subscribe_events(events.append)
            raw_before = (await app._agent.checkpointer.aget_tuple(original.config))
            history = await app.aget_session_history(sid)
            assert history['recovery']['actions'] == ['restore_approval', 'continue']
            kwargs = dict(checkpoint_id=history['checkpoint_id'], action='restore_approval')
            recovered = await app.arecover_session(sid, **kwargs)
            assert recovered['state'] == 'interrupted' and recovered['checkpoint_id'] == history['checkpoint_id']
            memory = app._sessions._sessions[sid]
            assert memory.resolve_permission_context()['session_allow'] == [] and memory.snapshot_for_run()['pending_turn'] is None and not memory.is_busy()
            assert memory.snapshot_for_run()['pending']['requested_run_id'] == row['run_id']
            assert app.pending_tool_approvals(sid)[0]['tool_call_id'] == 'write-call'
            assert not target.exists() and events == []
            assert (model.seen == []) if streaming_model else model.i == 0
            raw_after = (await app._agent.checkpointer.aget_tuple(original.config))
            assert raw_before == raw_after
            result = (await app.aresume_tool_approval(sid, [decision]))
            results = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            # Official HITL writes rejected results before dispatching allowed calls.
            assert [m.tool_call_id for m in results] == (['read-call', 'write-call'] if decision == 'approve'
                                                        else ['write-call', 'read-call'])
            by_id = {m.tool_call_id: m for m in results}
            assert by_id['read-call'].content.find('saved input') >= 0
            assert by_id['write-call'].status == ('success' if decision == 'approve' else 'error')
            assert target.exists() == (decision == 'approve')
            if target.exists():
                assert target.read_text() == 'approved result'
            turns = [e for e in events if e.event_type == 'turn_started']
            assert [e.data.kind for e in turns] == ['approval_retry', 'model']
            starts = [e.data.tool_call_id for e in events if e.event_type == 'tool_call_started']
            assert starts.count('read-call') == 1 and starts.count('write-call') == 1
            assert not app.pending_tool_approvals(sid)
            assert app._storage.get(sid)['state'] == 'ready'
    await scenario()


@pytest.mark.parametrize('change', ['deny', 'target', 'environment', 'subject', 'run', 'session_allow', 'execution_kind'])
@async_test
async def test_revalidation_failure_has_no_writes_or_installed_session(monkeypatch, tmp_path, change):
    row, original = await approval_snapshot(monkeypatch, tmp_path)
    settings = AppSettings.model_validate({'permissions': {'deny': [{'tool': 'read'}]}}) if change == 'deny' else AppSettings()
    async with open_async_persistent_application('unused', database_path=tmp_path / 'approval.sqlite', tools=[create_session_read_tool()],
            system_prompt='', response_format=None, settings=settings) as app:
        storage = app._storage
        if change == 'run':
            storage.connection.execute('UPDATE naiyue_sessions SET run_id=NULL WHERE session_id=?', (row['session_id'],))
            storage.connection.commit()
        elif change == 'execution_kind':
            storage.connection.execute("UPDATE naiyue_sessions SET execution_root_kind='worktree' WHERE session_id=?", (row['session_id'],))
            storage.connection.commit()
        elif change not in ('deny', 'target'):
            snapshot = original._replace(values=deepcopy(original.values))
            call = snapshot.values['tool_execution']['calls'][0]
            if change == 'environment':
                call['subject']['execution_environment']['kind'] = 'other'
            elif change == 'subject':
                call['subject']['side_effects'] = 'may_mutate'
            else:
                snapshot.values['tool_execution']['session_allow'] = [{'previous': 'temporary scope'}]
            async def snapshot_read(*_a):
                return snapshot
            monkeypatch.setattr(app._agent, 'aget_state', snapshot_read)
        if change == 'target':
            (tmp_path / 'outside').symlink_to(tmp_path / 'different')
        before = database_facts(storage)
        with pytest.raises(SessionPersistenceError, match='approval_restore_unavailable|session_recovery_blocked'):
            await app.arecover_session(row['session_id'], checkpoint_id=original.config['configurable']['checkpoint_id'], action='restore_approval')
        assert database_facts(storage) == before and not app._sessions.has_session(row['session_id'])


@async_test
async def test_failed_approval_metadata_save_can_be_retried_without_modifying_graph(monkeypatch, tmp_path):
    row, original = await approval_snapshot(monkeypatch, tmp_path)
    async with build(monkeypatch, tmp_path / 'approval.sqlite', tools=[create_session_read_tool()]) as app:
        storage = app._storage
        raw = await app._agent.checkpointer.aget_tuple(original.config)
        with monkeypatch.context() as patch:
            patch.setattr(storage, 'finish_run', lambda *_a: (_ for _ in ()).throw(OSError('PRIVATE_SQL')))
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                await app.arecover_session(row['session_id'], checkpoint_id=original.config['configurable']['checkpoint_id'], action='restore_approval')
        assert not app._sessions.has_session(row['session_id']) and await app._agent.checkpointer.aget_tuple(original.config) == raw
        await app.arecover_session(row['session_id'], checkpoint_id=original.config['configurable']['checkpoint_id'], action='restore_approval')
        assert app.pending_tool_approvals(row['session_id'])


@pytest.mark.parametrize('source', ['config_allow', 'session_allow', 'auto'])
@async_test
async def test_old_allow_must_be_proven_by_current_local_policy_without_auto(monkeypatch, tmp_path, source):
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer, prepare_permission_call
    from naiyue_test_agent.tools.permissions import remembered_rule
    root = tmp_path / 'work'
    root.mkdir()
    first, second = tmp_path / 'first', tmp_path / 'second'
    settings = AppSettings.model_validate({'permissions': (
        {'allow': [{'tool': 'read', 'path': str(first)}]} if source == 'config_allow' else
        {'mode': 'Auto', 'auto': {'fast_model_id': 'A', 'detailed_model_id': 'A'}} if source == 'auto' else {})})
    request = AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(first)}, 'id': 'one'},
        {'name': 'read', 'args': {'path': str(second)}, 'id': 'two'}])
    path = tmp_path / 'state.sqlite'
    tools = [create_session_read_tool()]
    model = ToolModel(responses=[request])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    async with open_async_persistent_application('unused', database_path=path, tools=tools, settings=settings,
            system_prompt='', response_format=None) as app:
        storage = app._storage
        sid = await app.acreate_workspace_session('A', root)
        if source == 'session_allow':
            call = prepare_permission_call(request.tool_calls[0], app._tool_registry.get('read'),
                typed_context(identity=RunIdentity(session_id=sid, execution_root=ExecutionRoot(root) if root is not None else None)), settings.permissions, [])
            owner, observation_run = app._sessions._admit(sid, 'allow-setup', 'message')
            run_id, observation = observation_run.run_id, observation_run.observation
            owner.remember_approved_rules(observation_run, [remembered_rule(call['subject'])])
            owner._cleanup_run(observation_run)
            owner.release_run(observation_run)
        with monkeypatch.context() as patch:
            async def review(_self, data, _context):
                return {'decision': 'allow' if data['current_call']['id'] == 'one' else 'ask', 'source': 'auto_fast'}
            patch.setattr(AutoPermissionReviewer, 'areview', review)
            await app.arun(sid, [('user', 'inspect both files')])
        row = storage.get(sid)
        original = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        assert [c['decision'] for c in original.values['tool_execution']['calls']] == ['allow', 'ask']
    async with open_async_persistent_application('unused', database_path=path, tools=tools,
            settings=settings if source == 'auto' else AppSettings(), system_prompt='', response_format=None) as app:
        storage = app._storage
        reached = []
        def forbidden(*_a, **_k):
            reached.append(True)
            raise AssertionError('Recovery must never invoke Auto')
        monkeypatch.setattr(AutoPermissionReviewer, 'areview', forbidden)
        monkeypatch.setattr(AutoPermissionReviewer, 'areview', forbidden)
        before = database_facts(storage)
        with pytest.raises(SessionPersistenceError, match='approval_restore_unavailable'):
            await app.arecover_session(sid, checkpoint_id=original.config['configurable']['checkpoint_id'], action='restore_approval')
        assert reached == [] and database_facts(storage) == before and not app._sessions.has_session(sid)


@pytest.mark.parametrize('phase', ['before_terminal_metadata', 'after_restore_metadata'])
@async_test
async def test_real_process_exit_keeps_pre_tool_approval_recoverable(monkeypatch, tmp_path, phase):
    import subprocess
    import sys
    from textwrap import dedent
    root = tmp_path / 'work'
    root.mkdir()
    (tmp_path / 'outside').write_text('approved after restart')
    path = tmp_path / 'crash.sqlite'
    program = dedent('''
        import asyncio, os, sys
        from pathlib import Path
        sys.path.insert(0, sys.argv[3])
        from langchain_core.messages import AIMessage
        from naiyue_test_agent import application
        from naiyue_test_agent.application.async_persistent import open_async_persistent_application
        from naiyue_test_agent.model_access import ModelCatalog
        from naiyue_test_agent.tools import create_session_read_tool
        from test_async_application import StreamingToolModel
        path, phase = Path(sys.argv[1]), sys.argv[2]
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(path.parent / 'outside')}, 'id': 'read'}])])
        application.load_model_catalog = lambda _: ModelCatalog('A', {'A': model})
        def opened():
            return open_async_persistent_application('unused', database_path=path,
                tools=[create_session_read_tool()], system_prompt='', response_format=None)
        async def scenario():
            async with opened() as app:
                sid = await app.acreate_workspace_session('A', path.parent / 'work')
                if phase == 'before_terminal_metadata':
                    app._storage.finish_run = lambda *_a, **_k: os._exit(91)
                await app.arun(sid, [('user', 'read external file')])
            async with opened() as app:
                storage = app._storage
                original = storage.finish_run
                def saved_then_exit(*args):
                    original(*args)
                    os._exit(91)
                storage.finish_run = saved_then_exit
                history = await app.aget_session_history(sid)
                await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
        asyncio.run(scenario())
    ''')
    from pathlib import Path
    child = subprocess.run([sys.executable, '-c', program, str(path), phase, str(Path(__file__).parent)],
                           capture_output=True, timeout=20)
    assert child.returncode == 91, child.stderr.decode()
    async with build(monkeypatch, path, tools=[create_session_read_tool()]) as app:
        storage = app._storage
        row = storage.list_sessions()[0]
        assert row['state'] == ('running' if phase == 'before_terminal_metadata' else 'interrupted')
        history = await app.aget_session_history(row['session_id'])
        assert history['recovery']['kind'] == 'pending_approval'
        await app.arecover_session(row['session_id'], checkpoint_id=history['checkpoint_id'], action='restore_approval')
        snapshot = await app._agent.aget_state({'configurable': {'thread_id': row['thread_id']}})
        assert not any(isinstance(m, ToolMessage) for m in snapshot.values['messages'])
        result = await app.aresume_tool_approval(row['session_id'], ['approve'])
        results = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        assert len(results) == 1 and results[0].content == 'approved after restart'
