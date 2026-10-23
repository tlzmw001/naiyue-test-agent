"""Only the current last task can be replaced; effects are never rolled back."""

from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage
from native_application_helpers import async_test
from test_persistent_sessions import build
from test_write_tool import RecordingFake as ToolModel
from naiyue_test_agent.application.conversation.reply_storage import latest


@async_test
async def test_replace_only_last_task_preserve_prefix_reopen_and_deduplicate(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    model = ToolModel(responses=[AIMessage(content='first'), AIMessage(content='old'), AIMessage(content='new')])
    async with build(monkeypatch, path, model) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'one')])
        await app.arun(sid, [('user', 'two')])
        old = latest(app._storage, sid)
        args = dict(request_id=str(uuid4()), expected_reply_id=old['reply_id'], expected_conversation_revision=0)
        result = await app.aregenerate_last_reply(sid, **args)
        assert [m.content for m in result['messages']] == ['one', 'first', 'two', 'new']
        assert all(m.content != 'old' for m in model.seen[-1])
        again = await app.aregenerate_last_reply(sid, **args)
        assert again['replayed'] and len(model.seen) == 3
        row = app._storage.get(sid)
        assert row['conversation_revision'] == 1 and row['state'] == 'ready'
        page = await app.aget_chat_page(sid, limit=20, body_bytes=20000, preview_chars=100)
        assert len(page['records']) == 4
        assert len(app._storage.chat_heads(sid)) == 1
    async with build(monkeypatch, path) as app:
        await app.arestore_session(sid)
        assert [m.content for m in await app.aget_messages(sid)] == ['one', 'first', 'two', 'new']
        assert (await app.aget_session_status(sid))['conversation_revision'] == 1


@async_test
async def test_stale_task_or_preparation_failure_preserves_original(monkeypatch, tmp_path):
    model = ToolModel(responses=[AIMessage(content='one'), AIMessage(content='two')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'one')])
        first = latest(app._storage, sid)
        await app.arun(sid, [('user', 'two')])
        with pytest.raises(ValueError):
            await app.aregenerate_last_reply(sid, request_id=str(uuid4()), expected_reply_id=first['reply_id'], expected_conversation_revision=0)
        last = latest(app._storage, sid)
        row = app._storage.get(sid)
        async def fail(*args, **kwargs):
            raise OSError('synthetic seed failure')
        with monkeypatch.context() as patch:
            patch.setattr(app._agent, 'aupdate_state', fail)
            with pytest.raises(OSError):
                await app.aregenerate_last_reply(sid, request_id=str(uuid4()), expected_reply_id=last['reply_id'], expected_conversation_revision=0)
        assert app._storage.get(sid)['thread_id'] == row['thread_id']
        assert app._storage.get(sid)['state'] == 'ready'
        assert not app._sessions.require(sid).is_busy()
        assert len(model.seen) == 2


@async_test
async def test_activation_sql_failure_rolls_back_visible_tail_and_root(monkeypatch, tmp_path):
    model = ToolModel(responses=[AIMessage(content='old'), AIMessage(content='new')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'task')])
        saved = app._storage.get(sid)
        before = app._storage._read('SELECT * FROM naiyue_chat_messages WHERE session_id=?', (sid,))
        request_id = str(uuid4())
        boundary = latest(app._storage, sid)
        app._storage.connection.executescript('''CREATE TRIGGER stop_regeneration BEFORE DELETE ON naiyue_chat_messages
            BEGIN SELECT RAISE(ABORT, 'PRIVATE_ACTIVATION'); END;''')
        with pytest.raises(Exception, match='session_save_unconfirmed'):
            await app.aregenerate_last_reply(sid, request_id=request_id, expected_reply_id=boundary['reply_id'], expected_conversation_revision=0)
        assert app._storage.get(sid)['thread_id'] == saved['thread_id']
        assert app._storage._read('SELECT * FROM naiyue_chat_messages WHERE session_id=?', (sid,)) == before
        assert not app._sessions.require(sid).is_busy() and len(model.seen) == 1
        from naiyue_test_agent.application.conversation.reply_storage import action
        assert action(app._storage, sid, request_id)['status'] == 'prepare_failed'


@async_test
async def test_cancel_during_activation_settles_root_and_preserves_replay_input(monkeypatch, tmp_path):
    import asyncio
    import importlib
    from threading import Event
    module = importlib.import_module('naiyue_test_agent.application.conversation.session_regeneration')
    entered, release = Event(), Event()
    original = module._commit_replacement
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    path = tmp_path / 'state.sqlite'
    model = ToolModel(responses=[AIMessage(content='old')])
    async with build(monkeypatch, path, model) as app:
        sid = await app.acreate_session('A')
        await app.arun(sid, [('user', 'original task')])
        old = app._storage.get(sid)['thread_id']
        boundary = latest(app._storage, sid)
        with monkeypatch.context() as patch:
            patch.setattr(module, '_commit_replacement', blocked)
            task = asyncio.create_task(app.aregenerate_last_reply(sid, request_id=str(uuid4()),
                expected_reply_id=boundary['reply_id'], expected_conversation_revision=0))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            assert app._sessions.require(sid).is_busy()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        row = app._storage.get(sid)
        owner = app._sessions.require(sid)
        assert row['thread_id'] != old and owner.get_thread_id() == row['thread_id']
        assert row['conversation_revision'] == 1 and row['state'] == 'running'
        assert not owner.is_busy() and len(model.seen) == 1
    async with build(monkeypatch, path) as app:
        history = await app.aget_session_history(sid)
        assert history['recovery']['kind'] == 'interrupted_execution'
        await app.arecover_session(sid, checkpoint_id=history['checkpoint_id'], action='continue')
        assert [m.content for m in await app.aget_messages(sid)] == ['original task']
        assert (await app.aget_session_status(sid))['conversation_revision'] == 1
