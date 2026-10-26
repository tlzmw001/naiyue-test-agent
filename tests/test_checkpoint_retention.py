"""Retention preserves the application consumers of old framework checkpoints."""

import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_application_run_events import ToolModel
from test_persistent_sessions import build
from naiyue_test_agent import application
from naiyue_test_agent.checkpoint_settings import CheckpointRetentionSettings
from naiyue_test_agent.settings import AppSettings, load_settings
from naiyue_test_agent.storage.conversation_sql.reply_storage import latest


def configure(monkeypatch, **values):
    settings = AppSettings(checkpoint_retention=CheckpointRetentionSettings(**values))
    monkeypatch.setattr(application, 'load_settings', lambda: settings)
    return settings


def checkpoints(app, thread):
    return app.host.sessions._persistence.storage.database.read_rows('SELECT checkpoint_id FROM checkpoints WHERE thread_id=? ORDER BY checkpoint_id', (thread,))


def test_settings_default_override_and_invalid_disable(tmp_path, caplog):
    assert AppSettings().checkpoint_retention.enabled is True
    source = tmp_path / 'settings.toml'
    source.write_text('[checkpoint_retention]\nenabled=false\ntrigger_checkpoints=16\nmax_delete_checkpoints=4\n')
    assert load_settings(source).checkpoint_retention == CheckpointRetentionSettings(
        enabled=False, trigger_checkpoints=16, max_delete_checkpoints=4)
    source.write_text('[checkpoint_retention]\ntrigger_checkpoints=-1\n')
    assert load_settings(source).checkpoint_retention.enabled is False
    assert 'checkpoint_retention_invalid' in caplog.text


@async_test
async def test_completed_runs_prune_keep_chat_usage_and_reopen(monkeypatch, tmp_path):
    configure(monkeypatch, trigger_checkpoints=8, max_delete_checkpoints=16)
    path = tmp_path / 'state.sqlite'
    model = ToolModel(responses=[AIMessage(content=f'answer {n}', usage_metadata={
        'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105}) for n in range(10)])
    async with build(monkeypatch, path, model) as app:
        sid = await app.host.sessions.create('A')
        for n in range(9):
            result = await app.host.runs.start(sid, [('user', f'question {n}')])
        row = app.host.sessions._persistence.storage.sessions.get(sid)
        assert len(checkpoints(app, row['thread_id'])) < 16
        expected = [m.content for m in result['messages']]
        page = await app.host.history.page(sid, limit=64, body_bytes=200000, preview_chars=100)
        assert len(page['records']) == 18
        before = await app.host.observability.usage(sid, model_limit=20)
        usage_rows = app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_model_invocations WHERE session_id=?', (sid,))
        usage_runs = app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_model_usage_runs WHERE session_id=?', (sid,))
        assert len(usage_rows) == 9 and sum(row['input_tokens'] for row in usage_rows) == 900
        assert row['checkpoint_id'] == checkpoints(app, row['thread_id'])[-1]['checkpoint_id']
    async with build(monkeypatch, path, model) as app:
        await app.host.sessions.restore(sid)
        assert [m.content for m in await app.host.history.messages(sid)] == expected
        after = await app.host.observability.usage(sid, model_limit=20)
        # as_of is the time of this query, not a persisted usage fact.
        assert {k: v for k, v in after.items() if k != 'as_of'} == {k: v for k, v in before.items() if k != 'as_of'}
        assert app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_model_invocations WHERE session_id=?', (sid,)) == usage_rows
        assert app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM naiyue_model_usage_runs WHERE session_id=?', (sid,)) == usage_runs
        continued = await app.host.runs.start(sid, [('user', 'continue')])
        assert [m.content for m in continued['messages'][:-2]] == expected
        assert continued['messages'][-1].content == 'answer 9'


@pytest.mark.parametrize('enabled,threshold', [(False, 2), (True, 10000)])
@async_test
async def test_disabled_or_below_threshold_retains_all_checkpoints(monkeypatch, tmp_path, enabled, threshold):
    configure(monkeypatch, enabled=enabled, trigger_checkpoints=threshold)
    model = ToolModel(responses=[AIMessage(content='one'), AIMessage(content='two')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.host.sessions.create('A')
        await app.host.runs.start(sid, [('user', 'one')])
        thread = app.host.sessions._persistence.storage.sessions.get(sid)['thread_id']
        first = {r['checkpoint_id'] for r in checkpoints(app, thread)}
        await app.host.runs.start(sid, [('user', 'two')])
        second = {r['checkpoint_id'] for r in checkpoints(app, thread)}
        assert first < second


@async_test
async def test_last_task_source_survives_repeated_regeneration_and_reopen(monkeypatch, tmp_path):
    configure(monkeypatch, trigger_checkpoints=2, max_delete_checkpoints=128)
    path = tmp_path / 'state.sqlite'
    model = ToolModel(responses=[AIMessage(content=s) for s in ('prefix', 'old', 'new', 'newest')])
    async with build(monkeypatch, path, model) as app:
        sid = await app.host.sessions.create('A')
        await app.host.runs.start(sid, [('user', 'one')])
        await app.host.runs.start(sid, [('user', 'two')])
        for revision, answer in enumerate(('new', 'newest')):
            boundary = app.host.sessions._persistence.storage.replies.latest(sid)
            source = boundary['source_thread_id'] or boundary['thread_id']
            assert boundary['source_checkpoint_id'] in {r['checkpoint_id'] for r in checkpoints(app, source)}
            result = await app.host.runs.regenerate(sid, request_id=str(uuid4()),
                expected_reply_id=boundary['reply_id'], expected_conversation_revision=revision)
            assert [m.content for m in result['messages']] == ['one', 'prefix', 'two', answer]
    async with build(monkeypatch, path, model) as app:
        await app.host.sessions.restore(sid)
        assert [m.content for m in await app.host.history.messages(sid)] == ['one', 'prefix', 'two', 'newest']
        page = await app.host.history.page(sid, limit=20, body_bytes=20000, preview_chars=100)
        assert len(page['records']) == 4


@async_test
async def test_delete_failure_rolls_back_maintenance_preserves_success(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.storage import checkpoint_retention
    configure(monkeypatch, trigger_checkpoints=2)
    write_snapshots = []
    original = checkpoint_retention._commit
    def observed(storage, *args):
        before = storage.database.read_rows('SELECT * FROM writes')
        try:
            return original(storage, *args)
        finally:
            write_snapshots.append((before, storage.database.read_rows('SELECT * FROM writes')))
    monkeypatch.setattr(checkpoint_retention, '_commit', observed)
    model = ToolModel(responses=[AIMessage(content='one'), AIMessage(content='two')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.host.sessions.create('A')
        await app.host.runs.start(sid, [('user', 'one')])
        write_snapshots.clear()
        thread = app.host.sessions._persistence.storage.sessions.get(sid)['thread_id']
        before = {r['checkpoint_id'] for r in checkpoints(app, thread)}
        app.host.sessions._persistence.storage.database.connection.executescript('''CREATE TRIGGER reject_retention BEFORE DELETE ON checkpoints
            BEGIN SELECT RAISE(ABORT, 'PRIVATE_RETENTION_FAILURE'); END;''')
        events = []
        app.host.observability.subscribe(events.append)
        result = await app.host.runs.start(sid, [('user', 'two')])
        assert result['messages'][-1].content == 'two'
        assert app.host.sessions._persistence.storage.sessions.get(sid)['state'] == 'ready'
        assert before <= {r['checkpoint_id'] for r in checkpoints(app, thread)}
        assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'
        assert not app.host.sessions._sessions.require(sid).is_busy()
        assert 'checkpoint_retention_failed' in caplog.text
        assert 'PRIVATE_RETENTION_FAILURE' not in caplog.text
        assert write_snapshots and all(before == after for before, after in write_snapshots)


@async_test
async def test_other_session_checkpoints_untouched(monkeypatch, tmp_path):
    configure(monkeypatch, trigger_checkpoints=2)
    model = ToolModel(responses=[AIMessage(content=str(n)) for n in range(4)])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        a, b = await app.host.sessions.create('A'), await app.host.sessions.create('A')
        await app.host.runs.start(b, [('user', 'B')])
        thread = app.host.sessions._persistence.storage.sessions.get(b)['thread_id']
        before = app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints WHERE thread_id=?', (thread,))
        writes = app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM writes WHERE thread_id=?', (thread,))
        for n in range(3):
            await app.host.runs.start(a, [('user', str(n))])
        assert app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM checkpoints WHERE thread_id=?', (thread,)) == before
        assert app.host.sessions._persistence.storage.database.read_rows('SELECT * FROM writes WHERE thread_id=?', (thread,)) == writes


@async_test
async def test_optional_prune_finishes_before_release_and_next_run_works(monkeypatch, tmp_path):
    from naiyue_test_agent.storage import checkpoint_retention
    configure(monkeypatch, trigger_checkpoints=2)
    entered, release = asyncio.Event(), asyncio.Event()
    original = checkpoint_retention.prune_after_finish
    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(checkpoint_retention, 'prune_after_finish', blocked)
    model = ToolModel(responses=[AIMessage(content='one'), AIMessage(content='two')])
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.host.sessions.create('A')
        task = asyncio.create_task(app.host.runs.start(sid, [('user', 'one')]))
        await asyncio.wait_for(entered.wait(), 5)
        assert app.host.sessions._persistence.storage.sessions.get(sid)['state'] == 'ready'
        assert app.host.sessions._sessions.require(sid).is_busy()
        release.set()
        await task
        assert not app.host.sessions._sessions.require(sid).is_busy()
        assert (await app.host.runs.start(sid, [('user', 'two')]))['messages'][-1].content == 'two'


@async_test
async def test_default_budget_keeps_up_with_120_turn_accumulating_context(monkeypatch, tmp_path):
    settings = configure(monkeypatch)
    model = ToolModel(responses=[AIMessage(content='a' * 4096 + str(n)) for n in range(120)])
    counts = []
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.host.sessions.create('A')
        for _ in range(120):
            await app.host.runs.start(sid, [('user', 'q' * 4096)])
            thread = app.host.sessions._persistence.storage.sessions.get(sid)['thread_id']
            counts.append(len(checkpoints(app, thread)))
        archived = app.host.sessions._persistence.storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_chat_messages WHERE session_id=?', (sid,)).fetchone()[0]
    assert archived == 240
    assert max(counts) < settings.checkpoint_retention.trigger_checkpoints, counts[-20:]
