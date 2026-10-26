"""A temporary application has the same own-chat chain without project access."""
import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent import application
from scripts.component_fixtures import build_component_application
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel, call


async def build(monkeypatch, tmp_path, replies, *, tools=()):
    settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')}})
    model = StreamingToolModel(replies=replies)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_component_application('unused', tools=tools, system_prompt='', response_format=None, settings=settings)
    return app, model


def test_temporary_chat_regeneration_and_isolation(monkeypatch, tmp_path):
    async def scenario():
        app, model = await build(monkeypatch, tmp_path, [AIMessage(content=s) for s in ('first', 'old', 'new')])
        try:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            await app.host.runs.start(sid, [('user', 'one')])
            await app.host.runs.start(sid, [('user', 'two')])
            last = app.host.sessions._persistence.storage.replies.latest(sid)
            args = dict(request_id=str(uuid4()), expected_reply_id=last['reply_id'], expected_conversation_revision=0)
            result = await app.host.runs.regenerate(sid, **args)
            assert [m.content for m in result['messages']] == ['one', 'first', 'two', 'new']
            assert (await app.host.runs.regenerate(sid, **args))['replayed'] and len(model.seen) == 3
            page = await app.host.history.page(sid, limit=20, body_bytes=20000, preview_chars=100)
            assert len(page['records']) == 4
            assert (await app.host.sessions.status(sid))['conversation_revision'] == 1
            assert app.host.sessions._persistence.storage.path is None and app.host.sessions._sessions._project is None
            assert app.host.memory._access is None and app.host.sessions._sessions.memory_jobs is None
        finally:
            await app.aclose()
        next_app, _ = await build(monkeypatch, tmp_path, [])
        try:
            assert await next_app.host.sessions.list() == []
            with pytest.raises(SessionPersistenceError, match='session_not_found'):
                await next_app.host.sessions.restore(sid)
        finally:
            await next_app.aclose()
        assert not (tmp_path / '.naiyue').exists()
        assert not (tmp_path / 'data').exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('existing_project', [False, True])
def test_temporary_project_tools_remain_unavailable(monkeypatch, tmp_path, existing_project):
    import json
    marker = tmp_path / '.naiyue' / 'workspace.json'
    original = json.dumps({'version': 1, 'workspace_id': str(uuid4())}).encode()
    if existing_project:
        marker.parent.mkdir()
        marker.write_bytes(original)
    async def scenario():
        from naiyue_test_agent.tools.history import create_history_tool
        from naiyue_test_agent.tools.memory import create_memory_tool
        replies = [call('history', {'action': 'list_sessions'}, 'h'), call('memory', {'action': 'list'}, 'm'), AIMessage(content='done')]
        app, _ = await build(monkeypatch, tmp_path, replies, tools=[create_history_tool(), create_memory_tool()])
        try:
            sid = await app.host.sessions.create_workspace('A', tmp_path)
            result = await app.host.runs.start(sid, [('user', 'query')])
            errors = [m.content for m in result['messages'] if m.type == 'tool']
            assert len(errors) == 2
            assert 'history_unavailable' in errors[0] and 'memory_unavailable' in errors[1]
            assert app.host.memory._access is None and app.host.sessions._sessions.memory_jobs is None
            assert app.host.sessions._persistence.storage.sessions.get_workspace_binding() is None
        finally:
            await app.aclose()
        if existing_project:
            assert marker.read_bytes() == original
        else:
            assert not marker.parent.exists()
        assert not (tmp_path / 'data' / 'personal').exists()
    asyncio.run(scenario())


def test_two_runs_interleave_with_both_writers_and_required_saves(monkeypatch, tmp_path, caplog):
    async def scenario():
        arrived, release = asyncio.Event(), asyncio.Event()
        from test_subagent_execution import ChildModel
        count = 0
        async def rendezvous(messages):
            nonlocal count
            count += 1
            if count == 2:
                arrived.set()
            await release.wait()
        model = ChildModel(replies=[AIMessage(content='answer one'), AIMessage(content='answer two')], on_request=rendezvous)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')}})
        app = await build_component_application('unused', tools=[], system_prompt='', response_format=None, settings=settings)
        tasks = []
        try:
            sessions = [await app.host.sessions.create('A'), await app.host.sessions.create('A')]
            tasks = [asyncio.create_task(app.host.runs.start(sid, [('user', text)])) for sid, text in zip(sessions, ('one', 'two'))]
            await asyncio.wait_for(arrived.wait(), 5)
            assert all(app.host.sessions._sessions.require(sid).is_busy() for sid in sessions)
            release.set()
            results = await asyncio.gather(*tasks)
            from native_application_helpers import await_diagnostics_idle
            await await_diagnostics_idle(app.host)
            for sid, result, prompt in zip(sessions, results, ('one', 'two')):
                assert result['messages'][0].content == prompt
                assert len(result['messages']) == 2
                assert app.host.sessions._persistence.storage.sessions.get(sid)['state'] == 'ready'
                usage = await app.host.observability.usage(sid, model_limit=20)
                assert usage['recorded_calls'] == 1
                assert usage['capture']['incomplete_runs'] == 0
                run, = (await app.host.observability.runs(sid))['runs']
                page = await app.host.observability.events(sid, run['run_id'], limit=100)
                assert run['status'] == 'completed' and not page['summary']['capture_incomplete']
                assert [event['event_type'] for event in page['events'] if event['event_type'] in ('run_started', 'run_finished')] == ['run_started', 'run_finished']
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await app.aclose()
        assert not any(marker in caplog.text for marker in ('database_transaction_failed', 'save_failed', 'observation_failed'))
    asyncio.run(scenario())


def test_temporary_required_save_failure_is_not_a_success(monkeypatch, tmp_path):
    async def scenario():
        app, _ = await build(monkeypatch, tmp_path, [AIMessage(content='answer')])
        events = []
        app.host.observability.subscribe(events.append)
        try:
            sid = await app.host.sessions.create('A')
            failure = OSError('PRIVATE_WRITE')
            def fail(*args, **kwargs):
                raise failure
            monkeypatch.setattr(app.host.sessions._persistence.storage.runs, 'finish_run', fail)
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed') as caught:
                await app.host.runs.start(sid, [('user', 'task')])
            assert caught.value.__cause__ is failure
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'failed'
            assert not app.host.sessions._sessions.require(sid).is_busy()
            assert not (await app.host.sessions.status(sid))['can_run']
            assert not any(e.event_type == 'run_finished' and e.data.status == 'completed' for e in events)
        finally:
            await app.aclose()
    asyncio.run(scenario())


def test_temporary_close_waits_for_required_save_then_closes_connections(monkeypatch, tmp_path):
    async def scenario():
        import threading
        import sqlite3
        app, _ = await build(monkeypatch, tmp_path, [AIMessage(content='answer')])
        sid = await app.host.sessions.create('A')
        entered, release, closing = asyncio.Event(), threading.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        save, stop = app.host.sessions._persistence.storage.runs.finish_run, app.host.sessions._sessions.stop_loaded_sessions
        def blocked(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return save(*args, **kwargs)
        def start_close():
            stop()
            closing.set()
        monkeypatch.setattr(app.host.sessions._persistence.storage.runs, 'finish_run', blocked)
        monkeypatch.setattr(app.host.sessions._sessions, 'stop_loaded_sessions', start_close)
        task = asyncio.create_task(app.host.runs.start(sid, [('user', 'task')]))
        close_task = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            close_task = asyncio.create_task(app.aclose())
            await asyncio.wait_for(closing.wait(), 5)
            assert not close_task.done() and not app.host.sessions._persistence.storage.database._closed
            assert app.host.sessions._sessions.require(sid).is_busy()
            release.set()
            assert (await task)['messages'][-1].content == 'answer'
            await close_task
            assert app.host.sessions._persistence.storage.database._closed and app.host.observability._usage._task.done() and app.host.observability._diagnostics._task.done()
            with pytest.raises(sqlite3.ProgrammingError):
                app.host.sessions._persistence.storage.database.connection.execute('SELECT 1')
            with pytest.raises(ValueError, match='no active connection'):
                await app.host.runs._agent.checkpointer.conn.execute('SELECT 1')
            await app.aclose()
        finally:
            release.set()
            await asyncio.gather(task, *([close_task] if close_task else []), return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


def test_no_session_cli_queries_real_ram_records_on_owner_loop(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.cli import chat as cli
    from test_application_cli import feed
    model = StreamingToolModel(replies=[])
    settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path / 'data')}})
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    feed(monkeypatch, ':usage', ':diagnostics', ':quit')
    assert cli.main(['--no-session', '--config', 'unused', '--workspace', str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '暂无已保存的调用用量记录' in output and '暂无已保留的运行诊断' in output
    assert '查询不可用' not in output and '读取失败' not in output
    assert model.seen == []
    assert not (tmp_path / '.naiyue').exists()
    assert not list((tmp_path / 'data').rglob('*.sqlite*'))
    assert (tmp_path / 'data/service/owner.lock').is_file()


@pytest.mark.parametrize('fault', ['custom_without_checkpoint', 'required_read'])
def test_temporary_required_state_failure_cannot_be_observation_degradation(monkeypatch, tmp_path, fault, caplog):
    async def scenario():
        app, _ = await build(monkeypatch, tmp_path, [AIMessage(content='answer')])
        events, calls = [], []
        app.host.observability.subscribe(events.append)
        async def custom(*args, **kwargs):
            calls.append(True)
            return {'messages': [], 'custom': 'unconfirmed'}
        async def unreadable(*args, **kwargs):
            calls.append(True)
            raise OSError('PRIVATE_REQUIRED_STATE')
        monkeypatch.setattr(app.host.runs._agent, 'ainvoke' if fault == 'custom_without_checkpoint' else 'aget_state',
                            custom if fault == 'custom_without_checkpoint' else unreadable)
        try:
            sid = await app.host.sessions.create('A')
            with pytest.raises(SessionPersistenceError, match='session_save_unconfirmed'):
                await app.host.runs.start(sid, [('user', 'task')])
            assert calls and sid in app.host.sessions._persistence._blocked
            assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'failed'
            assert not (await app.host.sessions.status(sid))['can_run']
            assert 'PRIVATE_REQUIRED_STATE' not in caplog.text
        finally:
            await app.aclose()
    asyncio.run(scenario())
