"""SQLite reopen never transfers saved MCP authority to a fresh connection."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import NAME, calls
from test_mcp_connections import facts, process_alive, stdio_settings


@asynccontextmanager
async def opened(monkeypatch, path, database, replies, *, settings=None, initial=(NAME,)):
    model = StreamingToolModel(replies=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    options = settings or AppSettings.model_validate({'mcp': {'servers': {'local': stdio_settings(
        path, 'stateful', allowed_tools=['echo']).model_dump()}}})
    async with open_component_application('unused', tools=[], initial_active_tools=initial,
        system_prompt='', response_format=None, settings=options, database_path=database) as app:
        yield app, model


def test_saved_mcp_approval_requires_new_call_and_never_restores_old_authority(monkeypatch, tmp_path):
    async def scenario():
        path, database = tmp_path / 'calls.jsonl', tmp_path / 'state.sqlite'
        async with opened(monkeypatch, path, database, [call(NAME, {}, 'old')]) as (app, _):
            sid = await app.host.sessions.create('A')
            paused = await app.host.runs.start(sid, [('user', 'old request')])
            assert paused['approval_requests'] and not calls(path)
        async with opened(monkeypatch, path, database, [call(NAME, {'text': 'new'}, 'new'), AIMessage(content='done')]) as (app, model):
            history = await app.host.history.session(sid)
            assert history['recovery']['kind'] == 'pending_approval'
            assert history['recovery']['reason'] == 'mcp_approval_requires_new_call'
            assert history['recovery']['actions'] == ['continue']
            with pytest.raises(SessionPersistenceError, match='approval_restore_unavailable'):
                await app.host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
            assert model.seen == [] and not calls(path)
            await app.host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='continue')
            assert not calls(path) and app.host.sessions._sessions.resolve_permission_context(sid)['session_allow'] == []
            result = await app.host.runs.start(sid, [('user', 'new request')])
            assert result['approval_requests'][0]['tool_call_id'] == 'new' and not calls(path)
            result = await app.host.runs.resume_approval(sid, ['approve'])
            messages = [m for m in result['messages'] if isinstance(m, ToolMessage)]
            assert [(m.tool_call_id, m.status) for m in messages] == [('old', 'error'), ('new', 'success')]
            assert messages[0].additional_kwargs['naiyue_recovery']['reason'] == 'cancelled_before_approval'
            assert len(calls(path)) == 1 and result['messages'][-1].content == 'done'
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['catalog', 'config', 'disabled', 'unselected'])
def test_changed_or_missing_service_never_executes_old_approval(monkeypatch, tmp_path, change):
    async def scenario():
        path, database = tmp_path / 'changed.jsonl', tmp_path / 'state.sqlite'
        async with opened(monkeypatch, path, database, [call(NAME, {}, 'old')]) as (app, _):
            sid = await app.host.sessions.create('A')
            await app.host.runs.start(sid, [('user', 'request')])
        server = stdio_settings(path, 'stateful', allowed_tools=['echo'])
        if change == 'catalog': path.with_suffix('.drift').touch()
        if change == 'config': server = server.model_copy(update={'call_timeout_seconds': 10.})
        if change == 'disabled': server = server.model_copy(update={'enabled': False})
        if change == 'unselected': server = server.model_copy(update={'allowed_tools': []})
        settings = AppSettings.model_validate({'mcp': {'servers': {'local': server.model_dump()}}})
        async with opened(monkeypatch, path, database, [], settings=settings, initial=()) as (app, model):
            history = await app.host.history.session(sid)
            assert history['messages'] and not calls(path) and not model.seen
            if change in ('disabled', 'unselected'):
                assert history['recovery']['reason'] == 'saved_configuration_unavailable'
                assert history['recovery']['actions'] == []
                with pytest.raises(SessionPersistenceError, match='invalid_active_tools'):
                    await app.host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
            else:
                assert history['recovery']['actions'] == ['continue']
                with pytest.raises(SessionPersistenceError, match='approval_restore_unavailable'):
                    await app.host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='restore_approval')
                await app.host.sessions.recover(sid, checkpoint_id=history['checkpoint_id'], action='continue')
            assert not calls(path) and not model.seen
    asyncio.run(scenario())


def test_completed_session_reopen_preserves_history_but_reapproves_and_starts_new_process(monkeypatch, tmp_path):
    async def scenario():
        path, database = tmp_path / 'completed.jsonl', tmp_path / 'state.sqlite'
        async with opened(monkeypatch, path, database, [call(NAME, {}, 'old'), AIMessage(content='first')]) as (app, _):
            sid = await app.host.sessions.create('A')
            await app.host.runs.start(sid, [('user', 'first')])
            await app.host.runs.resume_approval(sid, ['approve'], remember_approved=['old'])
            original_pid = calls(path)[0]['pid']
        assert not process_alive(original_pid)
        async with opened(monkeypatch, path, database, [call(NAME, {'text': 'new'}, 'new'), AIMessage(content='second')]) as (app, _):
            await app.host.sessions.restore(sid)
            assert app.host.sessions._sessions.resolve_permission_context(sid)['session_allow'] == []
            restored = await app.host.history.messages(sid)
            old, = [m for m in restored if isinstance(m, ToolMessage)]
            assert old.artifact['source'] == {'server_id': 'local', 'remote_tool': 'echo'}
            result = await app.host.runs.start(sid, [('user', 'second')])
            assert result['approval_requests'] and len(calls(path)) == 1
            result = await app.host.runs.resume_approval(sid, ['approve'])
            assert result['messages'][-1].content == 'second'
            assert [r['count'] for r in calls(path)] == [1, 1]
            assert calls(path)[1]['pid'] != original_pid
        assert all(not process_alive(r['pid']) for r in facts(path) if r['event'] == 'start')
    asyncio.run(scenario())


def test_root_necessary_save_failure_is_not_hidden_by_mcp_success(monkeypatch, tmp_path):
    async def scenario():
        path, database = tmp_path / 'save.jsonl', tmp_path / 'state.sqlite'
        settings = AppSettings.model_validate({'mcp': {'servers': {'local': stdio_settings(path, 'stateful',
            allowed_tools=['echo']).model_dump()}}, 'permissions': {'mode': 'BypassApprovals'}})
        async with opened(monkeypatch, path, database, [call(NAME, {}), AIMessage(content='done')], settings=settings) as (app, _):
            sid = await app.host.sessions.create('A')
            events = []; app.host.observability.subscribe(events.append)
            def fail(*args, **kwargs): raise OSError('PRIVATE-SAVE-BODY')
            monkeypatch.setattr(app.host.sessions._persistence.storage.runs, 'finish_run', fail)
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                await app.host.runs.start(sid, [('user', 'call')])
            assert len(calls(path)) == 1
            finished = [e.data.status for e in events if e.event_type == 'run_finished']
            assert finished == ['failed']
            assert not (await app.host.sessions.status(sid))['can_run']
            with pytest.raises(SessionPersistenceError): await app.host.runs.start(sid, [('user', 'again')])
            assert len(calls(path)) == 1
    asyncio.run(scenario())
