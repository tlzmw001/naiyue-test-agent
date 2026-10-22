"""Duration follows existing usage identity, SQL migration and finite diagnostics."""

import sqlite3
import json
from dataclasses import replace

import pytest

from naiyue_test_agent.agent_runtime.events import (
    AgentEventSource, RunEventPublisher, ModelInvocationStartedData, ModelInvocationFinishedData,
    RunStartedData, RunFinishedData,
)
from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.application.diagnostic_persistence import project_diagnostic_event
from test_model_usage_storage import seed, facts
from test_diagnostic_storage import limits


def test_schema12_migration_preserves_old_usage_as_unknown(tmp_path):
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
        for event in facts():
            store.save_model_usage_event(event)
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE naiyue_subagent_instances')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_model_invocations)')}
        if 'duration_ms' in columns:
            connection.execute('ALTER TABLE naiyue_model_invocations DROP COLUMN duration_ms')
        connection.execute('UPDATE naiyue_store_info SET version=12')
        before = connection.execute('SELECT invocation_id,started_at,finished_at,input_tokens FROM naiyue_model_invocations').fetchall()
    with SessionStorage(path) as store:
        assert store.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 14
        assert store.connection.execute('SELECT duration_ms FROM naiyue_model_invocations').fetchone()[0] is None
        assert [tuple(r) for r in store.connection.execute('SELECT invocation_id,started_at,finished_at,input_tokens FROM naiyue_model_invocations')] == before
        assert store.read_model_usage('s', model_limit=20)['duration'] == {
            'known_sum_ms': None, 'reported_calls': 0, 'unknown_calls': 1}
    with SessionStorage(path) as store:
        assert store.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 14


def test_duration_is_part_of_idempotent_terminal_and_survives_reopen(tmp_path):
    path = tmp_path / 'state.sqlite'
    start, end = facts()
    end = replace(end, data=replace(end.data, duration_ms=0))
    with SessionStorage(path) as store:
        seed(store)
        for event in (start, end, end, start):
            store.save_model_usage_event(event)
        assert store.connection.execute('SELECT duration_ms FROM naiyue_model_invocations').fetchone()[0] == 0
        conflict = replace(end, data=replace(end.data, duration_ms=1))
        with pytest.raises(ValueError, match='usage_terminal_conflict'):
            store.save_model_usage_event(conflict)
    with SessionStorage(path) as store:
        assert store.read_model_usage('s', model_limit=20)['duration'] == {
            'known_sum_ms': '0', 'reported_calls': 1, 'unknown_calls': 0}


def test_duration_buckets_keep_large_totals_partial_counts_and_child_scope(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        for index, (duration, purpose, child) in enumerate([
            (2**63 - 1, 'agent', False), (2**63 - 1, 'summary', True), (None, 'auto_fast', False),
        ]):
            events = []
            pub = RunEventPublisher('s', 'r', (events.append,))
            pub.publish('model_call_started', ModelInvocationStartedData(f'call{index}', 'A', purpose))
            pub.publish('model_call_finished', ModelInvocationFinishedData(f'call{index}', 'returned', duration_ms=duration))
            for event in events:
                if child:
                    event = replace(event, source=AgentEventSource('root/tester/child', 'child', 'cr', 'r', 'delegate'))
                store.save_model_usage_event(event)
        events = []
        RunEventPublisher('s', 'r', (events.append,)).publish('model_call_started', ModelInvocationStartedData('pending', 'A', 'agent'))
        store.save_model_usage_event(events[0])
        value = store.read_model_usage('s', model_limit=20)
        assert value['duration'] == {'known_sum_ms': str(2 * (2**63 - 1)), 'reported_calls': 2, 'unknown_calls': 2}
        assert value['models']['items'][0]['duration'] == value['duration']
        assert {r['id']: r['duration']['reported_calls'] for r in value['scopes']} == {'root': 1, 'child': 1}
        assert {r['id']: r['duration']['unknown_calls'] for r in value['activities']} == {
            'root_agent': 1, 'summary': 0, 'auto_fast': 1}
        writes = store.connection.total_changes
        assert store.read_model_usage('s', model_limit=20)['duration'] == value['duration']
        assert store.connection.total_changes == writes


def test_run_diagnostic_summary_and_history_keep_unknown_duration(tmp_path):
    events = []
    pub = RunEventPublisher('s', 'r', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    pub.publish('run_finished', RunFinishedData('completed', duration_ms=43))
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
        assert store.save_diagnostic_event(project_diagnostic_event(events[0]), limits())
        store.finish_diagnostic_run(project_diagnostic_event(events[1]), limits())
        assert store.read_diagnostic_runs('s')['runs'][0]['duration_ms'] == 43
        detail = store.read_diagnostic_events('s', 'r')
        assert detail['summary']['duration_ms'] == detail['events'][-1]['data']['duration_ms'] == 43
    with SessionStorage(path) as store:
        assert store.read_diagnostic_runs('s')['runs'][0]['duration_ms'] == 43


def test_historical_diagnostic_duration_is_unknown_without_rewriting_json(tmp_path):
    events = []
    pub = RunEventPublisher('s', 'r', (events.append,))
    pub.publish('run_started', RunStartedData('message'))
    pub.publish('run_finished', RunFinishedData('completed'))
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        store.save_diagnostic_event(project_diagnostic_event(events[0]), limits())
        store.finish_diagnostic_run(project_diagnostic_event(events[1]), limits())
        row = store.connection.execute('SELECT summary_json FROM naiyue_diagnostic_runs').fetchone()
        historical = json.loads(row[0])
        del historical['duration_ms']
        old_json = json.dumps(historical)
        with store.connection:
            store.connection.execute('UPDATE naiyue_diagnostic_runs SET summary_json=?', (old_json,))
        writes = store.connection.total_changes
        assert store.read_diagnostic_runs('s')['runs'][0]['duration_ms'] is None
        assert store.read_diagnostic_events('s', 'r')['summary']['duration_ms'] is None
        assert store.connection.total_changes == writes
        assert store.connection.execute('SELECT summary_json FROM naiyue_diagnostic_runs').fetchone()[0] == old_json
