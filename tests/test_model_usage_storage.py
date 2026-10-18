"""Independent invocation facts survive reopen and reject conflicting identities."""

from legacy_store_schema import remove_reply_schema

import sqlite3
import pytest

from naiyue_test_agent.agent_runtime.events import (
    RunEventPublisher, ModelInvocationStartedData, ModelInvocationFinishedData,
)
from naiyue_test_agent.application.session_storage import SessionStorage


def seed(store, sid='s'):
    store.create(sid, {'thread_id': 'thread-' + sid, 'model_id': 'A', 'workspace_root': None,
        'active_tool_names': (), 'permission_mode': 'Default', 'thinking_level': 'model_default'})


def facts(sid='s', rid='r', identity='call', *, input_tokens=100):
    events = []
    publisher = RunEventPublisher(sid, rid, (events.append,))
    publisher.publish('model_invocation_started', ModelInvocationStartedData(identity, 'A', 'agent'))
    publisher.publish('model_invocation_finished', ModelInvocationFinishedData(identity, 'returned', 'final',
        input_tokens=input_tokens, output_tokens=0, cache_read_tokens=20, cache_write_tokens=10))
    return events


def rows(store):
    return store._read('SELECT * FROM naiyue_model_invocations ORDER BY invocation_id')


def test_invocation_pair_is_idempotent_and_survives_reopen(tmp_path):
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
        for event in facts() * 2:
            store.save_model_usage_event(event)
        store.finish_model_usage_run('s', 'r')
        row, = rows(store)
        assert row['status'] == 'returned' and row['input_tokens'] == 100 and row['output_tokens'] == 0
        assert row['model_id'] == 'A' and row['purpose'] == 'agent' and row['agent_path'] == 'root'
        assert row['started_at'] and row['finished_at'] and row['usage_status'] == 'final'
        run, = store._read('SELECT * FROM naiyue_model_usage_runs')
        assert run['capture_incomplete'] == 0 and run['dropped_events'] == 0 and run['recording_closed_at']
    with SessionStorage(path) as store:
        assert rows(store) == [row]


def test_identity_or_terminal_conflict_never_overwrites_first_fact(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store); seed(store, 'other')
        for event in facts():
            store.save_model_usage_event(event)
        before = rows(store)
        for event in (facts('other')[0], facts(input_tokens=200)[1]):
            with pytest.raises(ValueError, match='conflict'):
                store.save_model_usage_event(event)
        assert rows(store) == before


def test_only_end_and_unfinished_start_remain_explicitly_incomplete(tmp_path):
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
        store.save_model_usage_event(facts(identity='only-end')[1])
        store.save_model_usage_event(facts(identity='only-start')[0])
    with SessionStorage(path) as store:
        ending, starting = rows(store)
        assert ending['status'] == 'returned' and ending['started_at'] is None and ending['input_tokens'] == 100
        assert starting['status'] == 'unconfirmed' and starting['finished_at'] is None
        assert starting['input_tokens'] is None
        assert store._read('SELECT capture_incomplete FROM naiyue_model_usage_runs')[0]['capture_incomplete'] == 1


def test_schema_eight_migrates_without_changing_sessions_or_chat(tmp_path):
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
    with sqlite3.connect(path) as connection:
        remove_reply_schema(connection)
        connection.executescript('''DROP TABLE IF EXISTS naiyue_model_invocations;
            DROP TABLE IF EXISTS naiyue_model_usage_runs;
            UPDATE naiyue_store_info SET version=8;''')
    with SessionStorage(path) as store:
        assert store.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 12
        assert store.get('s')['thread_id'] == 'thread-s' and store.chat_heads('s') == []
        assert rows(store) == []


def test_large_sqlite_counter_is_unknown_instead_of_overflowing(tmp_path, caplog):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        for event in facts(input_tokens=2**80):
            store.save_model_usage_event(event)
        row, = rows(store)
        assert row['input_tokens'] is None and row['output_tokens'] == 0
    assert 'model_usage_storage_count_invalid' in caplog.text


def test_usage_settings_are_configurable_and_invalid_budgets_fall_back(tmp_path, caplog):
    from naiyue_test_agent.settings import load_settings
    path = tmp_path / 'settings.toml'
    path.write_text('[usage_persistence]\nmax_pending_events=3\nmax_pending_bytes=2048\n')
    assert load_settings(path).usage_persistence.max_pending_events == 3
    path.write_text('[usage_persistence]\nmax_pending_events=0\n')
    assert load_settings(path).usage_persistence.max_pending_events == 1024
    assert 'usage_persistence_settings_fallback' in caplog.text


def test_late_start_fills_identity_without_replacing_saved_terminal(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        start, end = facts()
        store.save_model_usage_event(end)
        terminal = rows(store)[0]
        store.save_model_usage_event(start)
        store.save_model_usage_event(end)
        row, = rows(store)
        assert row['model_id'] == 'A' and row['started_at']
        assert (row['status'], row['finished_at'], row['input_tokens']) == (
            terminal['status'], terminal['finished_at'], terminal['input_tokens'])


def test_usage_ddl_failure_rolls_back_without_damaging_existing_schema(monkeypatch, tmp_path):
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
    with sqlite3.connect(path) as connection:
        remove_reply_schema(connection)
        connection.executescript('''DROP TABLE naiyue_model_invocations; DROP TABLE naiyue_model_usage_runs;
            UPDATE naiyue_store_info SET version=8;''')
    connect = sqlite3.connect
    def denied(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TABLE and name == 'naiyue_model_invocations' else sqlite3.SQLITE_OK)
        return connection
    from naiyue_test_agent.application.errors import SessionPersistenceError
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', denied)
        with pytest.raises(SessionPersistenceError, match='storage_unavailable'):
            SessionStorage(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 8
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='naiyue_model_usage_runs'").fetchone() is None
        assert connection.execute('SELECT session_id FROM naiyue_sessions').fetchone()[0] == 's'


@pytest.mark.parametrize('status,usage_status', [('failed', 'partial'), ('cancelled', 'partial'), ('failed', 'unavailable')])
def test_partial_and_unknown_counts_stay_distinct_from_zero(tmp_path, status, usage_status):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        events = []
        publisher = RunEventPublisher('s', 'r', (events.append,))
        publisher.publish('model_invocation_started', ModelInvocationStartedData('call', 'A', 'summary'))
        publisher.publish('model_invocation_finished', ModelInvocationFinishedData('call', status, usage_status,
            input_tokens=100 if usage_status == 'partial' else None,
            output_tokens=0 if usage_status == 'partial' else None, error_type='RuntimeError'))
        for event in events:
            store.save_model_usage_event(event)
        row, = rows(store)
        assert (row['status'], row['usage_status']) == (status, usage_status)
        assert row['output_tokens'] == (0 if usage_status == 'partial' else None)
        assert row['cache_read_tokens'] is None
