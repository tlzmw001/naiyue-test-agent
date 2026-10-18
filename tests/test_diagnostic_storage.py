"""Finite diagnostic groups are separate from execution state and chat/usage."""

from legacy_store_schema import remove_reply_schema

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from naiyue_test_agent.agent_runtime.events import RuntimeEvent, RunStartedData, RunFinishedData, ToolCallStartedData
from naiyue_test_agent.application.session_storage import SessionStorage
from test_model_usage_storage import seed


def limits(**values):
    from naiyue_test_agent.settings import DiagnosticSettings
    return DiagnosticSettings(**values)


def fact(kind='run_started', data=None, *, sid='s', rid='r', sequence=1, source=None):
    from naiyue_test_agent.application.diagnostic_persistence import project_diagnostic_event
    event = RuntimeEvent(kind, sid, rid, sequence, datetime.now(timezone.utc).isoformat(),
                         data if data is not None else RunStartedData('message'), source)
    return project_diagnostic_event(event)


def end(*, sid='s', rid='r', sequence=100):
    return fact('run_finished', RunFinishedData('completed'), sid=sid, rid=rid, sequence=sequence)


def test_schema_nine_migrates_and_reopen_preserves_facts(tmp_path):
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
    with sqlite3.connect(path) as connection:
        remove_reply_schema(connection)
        connection.executescript('DROP TABLE IF EXISTS naiyue_diagnostic_events; '
                                'DROP TABLE IF EXISTS naiyue_diagnostic_runs; '
                                'UPDATE naiyue_store_info SET version=9;')
    with SessionStorage(path) as store:
        assert store.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 12
        cfg = limits()
        store.save_diagnostic_event(fact(), cfg)
        store.save_diagnostic_event(fact('tool_call_started', ToolCallStartedData('t', 'read'), sequence=3), cfg)
        store.finish_diagnostic_run(end(), cfg)
        page = store.read_diagnostic_events('s', 'r', limit=2)
        assert page['summary']['status'] == 'completed' and not page['summary']['capture_incomplete']
        assert [e['sequence'] for e in page['events']] == [1, 3]
        assert page['next_after'] == 3
        assert store.read_diagnostic_events('s', 'r', after=3)['events'][0]['sequence'] == 100
        before = store.get('s')
    with SessionStorage(path) as store:
        assert store.get('s') == before
        assert store.read_diagnostic_events('s', 'r')['summary']['status'] == 'completed'


def test_duplicate_and_filtered_sequence_gaps_are_not_capture_loss(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        cfg, record = limits(), fact()
        store.save_diagnostic_event(record, cfg)
        store.save_diagnostic_event(record, cfg)
        store.finish_diagnostic_run(end(sequence=30), cfg)
        store.finish_diagnostic_run(end(sequence=30), cfg)
        page = store.read_diagnostic_events('s', 'r')
        assert [r['sequence'] for r in page['events']] == [1, 30]
        assert page['summary']['event_count'] == 2
        assert page['summary']['capture_incomplete'] is False


def test_per_run_budget_stops_details_but_retains_terminal_summary(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        cfg = limits(max_run_bytes=8192)
        store.save_diagnostic_event(fact(), cfg)
        for i in range(2, 62):
            store.save_diagnostic_event(fact('tool_call_started', ToolCallStartedData('t' * 100 + str(i), 'read'), sequence=i), cfg)
        store.finish_diagnostic_run(end(), cfg)
        page = store.read_diagnostic_events('s', 'r', limit=100)
        assert 1 < len(page['events']) < 62
        summary = page['summary']
        assert summary['payload_bytes'] <= 8192 and summary['status'] == 'completed'
        assert summary['capture_incomplete'] and summary['reason'] == 'run_budget'
        assert summary['dropped_events'] > 0


def test_workspace_eviction_removes_whole_closed_groups_and_protects_open_root(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        cfg = limits(max_run_bytes=8192, max_workspace_bytes=10000)
        store.save_diagnostic_event(fact(rid='active'), cfg)
        for i in range(20):
            rid = 'closed-' + str(i)
            store.save_diagnostic_event(fact(rid=rid), cfg)
            store.finish_diagnostic_run(end(rid=rid), cfg)
        page = store.read_diagnostic_runs('s', limit=100)
        ids = {r['run_id'] for r in page['runs']}
        assert 'active' in ids and 'closed-0' not in ids
        assert store.read_diagnostic_events('s', 'active')['summary']['capture_state'] == 'open'
        assert store.connection.execute('SELECT COUNT(*) FROM naiyue_diagnostic_events WHERE run_id=?', ('closed-0',)).fetchone()[0] == 0


def test_ttl_and_startup_unclosed_marker_do_not_change_session_or_chat(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        cfg = limits()
        store.save_diagnostic_event(fact(rid='open'), cfg)
        store.save_diagnostic_event(fact(rid='closed'), cfg)
        store.finish_diagnostic_run(end(rid='closed'), cfg)
        before = store.get('s')
        store.prepare_diagnostics(cfg)
        summary = store.read_diagnostic_events('s', 'open')['summary']
        assert summary['capture_state'] == 'interrupted' and summary['status'] is None
        assert summary['capture_incomplete'] and summary['reason'] == 'missing_terminal'
        assert store.get('s') == before and store.chat_heads('s') == []
        store.prune_diagnostics(cfg, now=datetime.now(timezone.utc) + timedelta(days=31))
        assert store.read_diagnostic_runs('s')['runs'] == []


def test_root_listing_and_event_queries_are_scoped_and_read_only(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store); seed(store, 'other')
        cfg = limits()
        for rid in ('one', 'two'):
            store.save_diagnostic_event(fact(rid=rid), cfg)
            store.finish_diagnostic_run(end(rid=rid), cfg)
        store.save_diagnostic_event(fact(sid='other', rid='foreign'), cfg)
        store.connection.execute('PRAGMA query_only=ON')
        first = store.read_diagnostic_runs('s', limit=1)
        assert [r['run_id'] for r in first['runs']] == ['two']
        second = store.read_diagnostic_runs('s', limit=1, before=first['next_before'])
        assert [r['run_id'] for r in second['runs']] == ['one'] and second['next_before'] is None
        assert store.read_diagnostic_events('s', 'one')['summary']['status'] == 'completed'
        with pytest.raises(Exception, match='diagnostic_run_not_found'):
            store.read_diagnostic_events('s', 'foreign')


def test_full_workspace_refuses_new_group_without_evicting_active_run(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        cfg = limits(max_run_bytes=8192, max_workspace_bytes=8192)
        assert store.save_diagnostic_event(fact(rid='active'), cfg)
        assert store.save_diagnostic_event(fact(rid='other'), cfg) is False
        runs = store.read_diagnostic_runs('s')['runs']
        assert [r['run_id'] for r in runs] == ['active']
        assert store.prune_diagnostics(cfg)['allocated_bytes'] <= cfg.max_workspace_bytes
        store.finish_diagnostic_run(end(rid='active'), cfg)
        assert store.save_diagnostic_event(fact(rid='other'), cfg)


def test_cleanup_batch_limit_and_missing_run_after_whole_group_eviction(tmp_path):
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        cfg = limits(cleanup_batch_runs=2)
        for i in range(5):
            store.save_diagnostic_event(fact(rid=str(i)), cfg)
            store.finish_diagnostic_run(end(rid=str(i)), cfg)
        future = datetime.now(timezone.utc) + timedelta(days=31)
        assert store.prune_diagnostics(cfg, now=future)['removed_runs'] == 2
        assert len(store.read_diagnostic_runs('s')['runs']) == 3
        with pytest.raises(Exception, match='diagnostic_run_not_found'):
            store.read_diagnostic_events('s', '0')
        assert store.prune_diagnostics(cfg, now=future)['removed_runs'] == 2
        assert store.prune_diagnostics(cfg, now=future)['removed_runs'] == 1


def test_projected_record_excludes_raw_targets_bodies_and_high_frequency_payloads():
    from naiyue_test_agent.agent_runtime.stream_events import MessageDeltaData, ToolProgressData
    from naiyue_test_agent.contracts.session_events import ApprovalRequestedData, ApprovalItemData
    from naiyue_test_agent.agent_runtime.events import AgentEventSource
    source = AgentEventSource('root/tester/child', 'child', 'child-run', 'r', 'outer')
    record = fact('approval_requested', ApprovalRequestedData(('t',), 'batch',
        (ApprovalItemData('a', 't', 'read', 'PRIVATE_TARGET', 'PRIVATE_WORKSPACE'),)), source=source)
    assert record['source']['agent_run_id'] == 'child-run'
    assert record['data']['approval_batch_id'] == 'batch'
    assert 'PRIVATE' not in json.dumps(record)
    assert fact('message_delta', MessageDeltaData('m', 'PRIVATE_BODY', 1)) is None
    assert fact('tool_progress', ToolProgressData('b', 't', 'bash', 'running', 'output', text='PRIVATE_OUTPUT')) is None


def test_source_projection_does_not_serialize_additional_dataclass_fields():
    from dataclasses import dataclass
    from naiyue_test_agent.agent_runtime.events import AgentEventSource
    @dataclass(frozen=True)
    class ExtendedSource(AgentEventSource):
        private_value: str = 'PRIVATE_SOURCE_VALUE'
    source = ExtendedSource('root/tester/child', 'child', 'child-run', 'r', 'outer')
    record = fact(source=source)
    assert set(record['source']) == {'agent_path', 'agent_id', 'agent_run_id', 'parent_run_id', 'parent_tool_call_id'}
    assert 'PRIVATE_SOURCE_VALUE' not in json.dumps(record)


def test_message_identity_is_linked_without_persisting_snapshot_body():
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.agent_runtime.message_observation import _snapshot
    record = fact('message_completed', _snapshot(AIMessage(id='message', content='PRIVATE_BODY'), 'graph_values'))
    assert record['data']['message_id'] == 'message'
    assert record['data']['message_type'] == 'ai'
    assert 'PRIVATE_BODY' not in json.dumps(record) and 'snapshot_json' not in record['data']


@pytest.mark.parametrize('value', [0, -1, True, '1024'])
def test_diagnostic_budget_validation_is_strict(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        limits(max_pending_events=value)


def test_diagnostic_sample_and_invalid_section_use_existing_config_loader(tmp_path, caplog):
    from pathlib import Path
    from naiyue_test_agent.settings import load_settings
    sample = Path(__file__).resolve().parents[1] / 'docs/examples/settings.example.toml'
    assert load_settings(sample).diagnostics.retention_days == 30
    path = tmp_path / 'settings.toml'
    path.write_text('max_active_runs=3\n[diagnostics]\nmax_run_bytes=0\n')
    settings = load_settings(path)
    assert settings.max_active_runs == 3 and settings.diagnostics.max_run_bytes == 1048576
    assert 'diagnostic_settings_fallback' in caplog.text
