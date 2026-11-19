import json
import sqlite3
from types import SimpleNamespace

from naiyue_test_agent.storage.cache_cleanup_inputs import input_dependencies


def test_retired_binding_keeps_pending_attachment_input(tmp_path):
    db = sqlite3.connect(':memory:')
    db.executescript("""
        CREATE TABLE naiyue_cloud_sources(source_handle, session_id, project_id, body_json);
        CREATE TABLE naiyue_cloud_operations(command_id, binding_id, session_id, body_json);
        CREATE TABLE naiyue_cloud_bindings(binding_id, session_id, project_id, retained, body_json);
        CREATE TABLE naiyue_cloud_deliveries(command_id, session_id, project_id, body_json);
        CREATE TABLE naiyue_cloud_receives(receive_id, session_id, project_id, body_json);
    """)
    identity = 'a' * 64
    db.execute('INSERT INTO naiyue_cloud_bindings VALUES (?, ?, ?, ?, ?)',
               ('b', 's', 'p', 0, json.dumps({'retained': False, 'local_attachments': {},
                'pending_attachment_releases': {'a': identity}})))
    budget = SimpleNamespace(remaining=100, check=lambda *args, **kwargs: None)
    packages, _ = input_dependencies(db, tmp_path, 'p', budget)
    assert identity in packages
    db.execute("UPDATE naiyue_cloud_bindings SET body_json=?", (json.dumps({'retained': False}),))
    packages, _ = input_dependencies(db, tmp_path, 'p', budget)
    assert not packages


def test_create_dependency_survives_missing_operation_and_legacy_candidates_are_independent(tmp_path):
    db = sqlite3.connect(':memory:')
    db.executescript("""
        CREATE TABLE naiyue_cloud_sources(source_handle, session_id, project_id, body_json);
        CREATE TABLE naiyue_cloud_operations(command_id, binding_id, session_id, body_json);
        CREATE TABLE naiyue_cloud_bindings(binding_id, session_id, project_id, retained, body_json);
        CREATE TABLE naiyue_cloud_deliveries(command_id, session_id, project_id, body_json);
        CREATE TABLE naiyue_cloud_receives(receive_id, session_id, project_id, body_json);
    """)
    active, legacy = 'a' * 64, 'b' * 64
    for bid, ident, phase in [('a', active, 'upload'), ('b', legacy, 'prepared')]:
        db.execute('INSERT INTO naiyue_cloud_bindings VALUES (?, ?, ?, ?, ?)',
                   (bid, 's', 'p', 1, json.dumps({'retained': True, 'input_id': ident,
                    'preparation': phase, 'create_command_id': bid})))
    for cid, receive in [('d1', 'r1'), ('d2', 'r2')]:
        db.execute('INSERT INTO naiyue_cloud_deliveries VALUES (?,?,?,?)',
                   (cid, 's', 'p', json.dumps({'action': 'apply', 'phase': 'partial', 'receive_id': receive})))
        db.execute('INSERT INTO naiyue_cloud_receives VALUES (?,?,?,?)',
                   (receive, 's', 'p', json.dumps({'binding_id': 'b', 'input_id': legacy,
                    'input_package': {'input_id': legacy}})))
    budget = SimpleNamespace(remaining=100, check=lambda *args, **kwargs: None)
    candidates = {}
    packages, _ = input_dependencies(db, tmp_path, 'p', budget, legacy_checks=candidates)
    assert '缺失' in packages[active]
    assert active not in candidates
    assert [command for command, _ in candidates[legacy]] == ['d1', 'd2']
