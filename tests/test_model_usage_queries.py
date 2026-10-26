"""Read committed usage without changing graph state or guessing missing counts."""

from dataclasses import replace
import json

import pytest

from naiyue_test_agent.agent_runtime.events import (
    AgentEventSource, ModelInvocationStartedData, ModelInvocationFinishedData, RunEventPublisher,
)
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.errors import SessionPersistenceError
from native_application_helpers import async_test
from test_model_usage_storage import seed
from test_persistent_sessions import build
from test_application_run_events import ToolModel
from langchain_core.messages import AIMessage


def record(store, identity, *, sid='s', model='A', purpose='agent', child=False,
           status='returned', usage='final', amount=100, output=0):
    events = []
    pub = RunEventPublisher(sid, 'r', (events.append,))
    pub.publish('model_call_started', ModelInvocationStartedData(identity, model, purpose))
    if status != 'started':
        pub.publish('model_call_finished', ModelInvocationFinishedData(identity, status, usage,
            input_tokens=amount, output_tokens=output, cache_read_tokens=None,
            cache_write_tokens=None if usage == 'unavailable' else 0))
    for event in events:
        if child:
            event = replace(event, source=AgentEventSource('root/tester/child', 'child', 'cr', 'r', 'delegate'))
        store.usage.save_model_usage_event(event)


def test_summary_keeps_partial_zero_unknown_and_disjoint_scope(tmp_path):
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        record(store, 'root', amount=1000, output=100)
        record(store, 'child', model='B', child=True, amount=2000, output=200)
        record(store, 'summary', purpose='summary', amount=500, output=50)
        record(store, 'fast', purpose='auto_fast', status='failed', usage='unavailable', amount=None, output=None)
        record(store, 'detail', purpose='auto_detailed', status='cancelled', usage='partial', amount=5)
        record(store, 'unknown', model=None, purpose=None, amount=None, output=None)
        store.usage.finish_model_usage_run('s', 'r', reason='buffer_overflow', dropped_events=2)
        value = store.usage.read_model_usage('s', model_limit=20)
        assert value['recorded_calls'] == 6
        assert value['metrics']['input_tokens'] == {'known_sum': '3505', 'reported_calls': 4, 'unknown_calls': 2}
        assert value['metrics']['cache_read_tokens'] == {'known_sum': None, 'reported_calls': 0, 'unknown_calls': 6}
        assert value['metrics']['cache_write_tokens']['known_sum'] == '0'
        assert value['status_counts']['failed'] == 1 and value['status_counts']['cancelled'] == 1
        assert value['usage_status_counts']['partial'] == 1
        assert {g['id']: g['recorded_calls'] for g in value['activities']} == {
            'root_agent': 1, 'child_agent': 1, 'summary': 1, 'auto_fast': 1, 'auto_detailed': 1, 'unattributed': 1}
        assert {g['id']: g['recorded_calls'] for g in value['scopes']} == {'root': 5, 'child': 1}
        assert value['models']['unattributed']['recorded_calls'] == 1
        assert value['capture'] == {'recorded_runs': 1, 'incomplete_runs': 1, 'dropped_events': '2'}


def test_model_pages_never_truncate_totals_and_reads_never_write(tmp_path):
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store); seed(store, 'other')
        for i, name in enumerate(['A', 'B', 'C']):
            record(store, 'c' + str(i), model=name, amount=2**62)
        record(store, 'foreign', sid='other', model='PRIVATE_MODEL')
        statements = []
        store.database.connection.set_trace_callback(statements.append)
        first = store.usage.read_model_usage('s', model_limit=2)
        second = store.usage.read_model_usage('s', model_limit=2, after_model=first['models']['next_after'])
        assert first['metrics'] == second['metrics']
        assert first['metrics']['input_tokens']['known_sum'] == str(3 * 2**62)
        assert [r['model_id'] for r in first['models']['items']] == ['A', 'B']
        assert [r['model_id'] for r in second['models']['items']] == ['C']
        assert second['models']['next_after'] is None
        assert 'PRIVATE_MODEL' not in json.dumps(first)
        assert not any(q.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'CREATE')) for q in statements)
        assert store.usage.read_model_usage('s', model_limit=2, after_model="' OR 1=1 --")['recorded_calls'] == 3


def test_empty_missing_and_reopened_unconfirmed_are_distinct(tmp_path):
    path = tmp_path / 'state.sqlite'
    with ProjectStore(path) as store:
        seed(store)
        empty = store.usage.read_model_usage('s', model_limit=20)
        assert empty['recorded_calls'] == 0 and empty['metrics']['input_tokens']['known_sum'] is None
        with pytest.raises(SessionPersistenceError, match='session_not_found'):
            store.usage.read_model_usage('missing', model_limit=20)
        record(store, 'pending', status='started')
    with ProjectStore(path) as store:
        value = store.usage.read_model_usage('s', model_limit=20)
        assert value['status_counts']['unconfirmed'] == 1 and value['capture']['incomplete_runs'] == 1
        assert value['metrics']['input_tokens']['known_sum'] is None


def test_one_read_snapshot_survives_a_concurrent_commit(monkeypatch, tmp_path):
    import sqlite3
    from naiyue_test_agent.storage.observability_sql import model_usage_storage
    path = tmp_path / 'state.sqlite'
    with ProjectStore(path) as store:
        store.database.connection.execute('PRAGMA journal_mode=WAL')
        seed(store)
        record(store, 'known')
        add = model_usage_storage._add_usage
        changed = False
        def commit(bucket, row):
            nonlocal changed
            if not changed:
                changed = True
                with sqlite3.connect(path) as writer:
                    writer.execute('UPDATE naiyue_model_invocations SET input_tokens=?', (900,))
            add(bucket, row)
        monkeypatch.setattr(model_usage_storage, '_add_usage', commit)
        value = store.usage.read_model_usage('s', model_limit=20)
        assert value['metrics']['input_tokens']['known_sum'] == '100'
        assert value['models']['items'][0]['metrics']['input_tokens']['known_sum'] == '100'
        assert store.usage.read_model_usage('s', model_limit=20)['metrics']['input_tokens']['known_sum'] == '900'


def test_native_sql_read_error_is_logged_without_body(tmp_path, caplog):
    import sqlite3
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        store.database.connection.set_authorizer(lambda action, table, *rest: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ and table == 'naiyue_model_invocations' else sqlite3.SQLITE_OK)
        with pytest.raises(SessionPersistenceError, match='usage_read_failed'):
            store.usage.read_model_usage('s', model_limit=20)
    assert 'model_usage_read_failed error_type=DatabaseError' in caplog.text


@pytest.mark.parametrize('cursor', ['', 'x' * 129, 1])
def test_invalid_model_cursor_does_not_read_any_rows(tmp_path, cursor):
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        with pytest.raises(ValueError):
            store.usage.read_model_usage('s', model_limit=20, after_model=cursor)


@pytest.mark.parametrize('limit', [0, -1, 101, True])
def test_invalid_limits_are_rejected(tmp_path, limit):
    with ProjectStore(tmp_path / 'state.sqlite') as store:
        seed(store)
        with pytest.raises(ValueError):
            store.usage.read_model_usage('s', model_limit=limit)


@async_test
async def test_application_query_is_read_only_cold_and_survives_reopen(monkeypatch, tmp_path):
    model = ToolModel(responses=[AIMessage(content='done', usage_metadata={
        'input_tokens': 100, 'output_tokens': 5, 'total_tokens': 105})])
    path = tmp_path / 'state.sqlite'
    async with build(monkeypatch, path, model) as app:
        sid = await app.host.sessions.create('A')
        await app.host.runs.start(sid, [('user', 'task')])
        expected = await app.host.observability.usage(sid, model_limit=20)
        assert expected['metrics']['input_tokens']['known_sum'] == '100'
    async with build(monkeypatch, path) as app:
        assert not app.host.sessions.is_loaded(sid)
        value = await app.host.observability.usage(sid, model_limit=20)
        assert value['metrics'] == expected['metrics'] and value['recorded_calls'] == 1
        assert not app.host.sessions.is_loaded(sid)
