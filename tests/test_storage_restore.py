"""Explicit maintenance preserves bytes and requires human confirmation under lock."""

from contextlib import closing, nullcontext
import json
import sqlite3
from uuid import uuid4

import pytest

from tests.storage_backup_fixture import artifact, source_store


def saved_store(tmp_path, *, version=12, bound=True):
    from naiyue_test_agent.storage_backup import create_backup
    from naiyue_test_agent.workspaces import locate_workspace
    with closing(source_store(tmp_path, version=version, bound=bound)[0]) as source:
        saved = create_backup(source.connection, source.path, target_schema=13)
        database = source.path
    location = locate_workspace(tmp_path / 'workspace', data_dir=None, database_path=database)
    return location, saved


def invoke(location, backup_id):
    from naiyue_test_agent.cli import main
    return main(['storage', 'restore', backup_id, '--workspace', str(location.root),
                 '--session-db', str(location.database_path)])


def maintenance(database):
    return database.with_name(database.name + '.maintenance')


def final_result(capsys):
    return json.loads(capsys.readouterr().out.splitlines()[-1])


@pytest.mark.parametrize('missing', [False, True])
def test_restore_corrupt_or_missing_target_preserves_original_group_and_snapshot(tmp_path, monkeypatch, capsys, missing):
    location, saved = saved_store(tmp_path)
    database = location.database_path
    if missing:
        database.unlink()
        originals = {}
    else:
        originals = {database.name + suffix: b'original' + suffix.encode()
                     for suffix in ('', '-wal', '-shm', '-journal')}
        for name, content in originals.items():
            database.with_name(name).write_bytes(content)
    lock_bytes = database.with_name(database.name + '.lock').read_bytes()
    snapshot = artifact(database, saved['backup_id']) / 'database.sqlite'
    expected = snapshot.read_bytes()
    def confirm(prompt):
        assert str(database) in capsys.readouterr().out
        assert not maintenance(database).exists()
        from naiyue_test_agent.storage_files import open_store_file, acquire_store_lock
        import os
        fd = open_store_file(database.with_name(database.name + '.lock'))
        try:
            with pytest.raises(BlockingIOError):
                acquire_store_lock(fd)
        finally:
            os.close(fd)
        return saved['backup_id']
    monkeypatch.setattr('builtins.input', confirm)
    assert invoke(location, saved['backup_id']) == 0
    result = final_result(capsys)
    assert result['status'] == 'completed' and result['backup_id'] == saved['backup_id']
    directory = maintenance(database) / result['operation_id']
    assert {p.name: p.read_bytes() for p in (directory / 'original').iterdir()} == originals
    assert database.read_bytes() == expected == snapshot.read_bytes()
    assert not (directory / 'candidate.sqlite').exists()
    assert not (maintenance(database) / 'restore.json').exists()
    assert json.loads((directory / 'operation.json').read_text())['phase'] == 'completed'
    assert database.with_name(database.name + '.lock').read_bytes() == lock_bytes
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 12
        assert connection.execute('SELECT state,run_id FROM naiyue_sessions').fetchone() == ('running', 'R')
        assert connection.execute('SELECT checkpoint FROM checkpoints').fetchone()[0] == b'opaque-checkpoint'
        assert connection.execute('SELECT value FROM writes').fetchone()[0] == b'opaque-value'


@pytest.mark.parametrize('answer', ['', 'yes', 'short', 'UPPER', 'space', 'eof', 'cancel'])
def test_refusal_eof_and_interrupt_do_not_create_maintenance_or_touch_target(tmp_path, monkeypatch, capsys, answer):
    location, saved = saved_store(tmp_path)
    database = location.database_path
    database.write_bytes(b'bad original')
    def respond(prompt):
        if answer == 'eof':
            raise EOFError
        if answer == 'cancel':
            raise KeyboardInterrupt
        return saved['backup_id'].upper() if answer == 'UPPER' else saved['backup_id'] + ' ' if answer == 'space' else answer
    monkeypatch.setattr('builtins.input', respond)
    assert invoke(location, saved['backup_id']) == (130 if answer == 'cancel' else 1)
    assert database.read_bytes() == b'bad original'
    assert not maintenance(database).exists()
    if answer != 'cancel':
        assert final_result(capsys)['error'] == 'restore_cancelled'


@pytest.mark.parametrize('kind,reason', [('unbound', 'backup_unbound'), ('foreign', 'backup_invalid'),
    ('tamper', 'backup_invalid'), ('bad-id', 'backup_invalid'), ('busy', 'storage_in_use')])
def test_invalid_selection_or_occupied_store_refused_before_confirmation(tmp_path, monkeypatch, capsys, kind, reason):
    location, saved = saved_store(tmp_path, bound=kind != 'unbound')
    database = location.database_path
    backup_id = saved['backup_id']
    if kind == 'foreign':
        marker = location.root / '.naiyue/workspace.json'
        marker.write_text(json.dumps({'version': 1, 'workspace_id': str(uuid4())}))
    if kind == 'tamper':
        (artifact(database, backup_id) / 'database.sqlite').write_bytes(b'bad')
    if kind == 'bad-id':
        backup_id = '../outside'
    def forbidden(prompt):
        raise AssertionError('invalid selection must not ask for confirmation')
    monkeypatch.setattr('builtins.input', forbidden)
    from naiyue_test_agent.application.session_storage import SessionStorage
    with closing(SessionStorage(database)) if kind == 'busy' else nullcontext():
        before = database.read_bytes()
        assert invoke(location, backup_id) == 1
        assert final_result(capsys)['error'] == reason
        assert database.read_bytes() == before
    assert not maintenance(database).exists()


def test_missing_workspace_never_creates_marker_or_database(tmp_path, monkeypatch, capsys):
    from naiyue_test_agent.cli import main
    database = tmp_path / 'absent.sqlite'
    assert main(['storage', 'restore', uuid4().hex, '--workspace', str(tmp_path), '--session-db', str(database)]) == 1
    assert final_result(capsys)['error'] == 'workspace_unavailable'
    assert not list(tmp_path.iterdir())


def test_workspace_copy_conflict_rejected_and_true_move_permitted(tmp_path, monkeypatch, capsys):
    import shutil
    from naiyue_test_agent.workspaces import locate_workspace
    location, saved = saved_store(tmp_path)
    copied = tmp_path / 'copy'
    shutil.copytree(location.root, copied)
    selected = locate_workspace(copied, data_dir=None, database_path=location.database_path)
    monkeypatch.setattr('builtins.input', lambda prompt: saved['backup_id'])
    assert invoke(selected, saved['backup_id']) == 1
    assert final_result(capsys)['error'] == 'workspace_id_conflict'
    moved = tmp_path / 'moved'
    location.root.rename(moved)
    selected = locate_workspace(moved, data_dir=None, database_path=location.database_path)
    assert invoke(selected, saved['backup_id']) == 0
    assert final_result(capsys)['status'] == 'completed'


def test_restore_has_no_model_runtime_dependency_and_revalidates_after_confirmation(tmp_path, monkeypatch, capsys):
    from naiyue_test_agent import settings
    location, saved = saved_store(tmp_path)
    database = location.database_path
    before = database.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('must not load runtime settings')
    monkeypatch.setattr(settings, 'load_settings', forbidden)
    def confirm(prompt):
        (artifact(database, saved['backup_id']) / 'database.sqlite').write_bytes(b'PRIVATE-tampered')
        return saved['backup_id']
    monkeypatch.setattr('builtins.input', confirm)
    assert invoke(location, saved['backup_id']) == 1
    assert final_result(capsys)['error'] == 'backup_invalid'
    assert database.read_bytes() == before
    assert not maintenance(database).exists()


def test_matching_old_main_is_preserved_before_candidate_is_installed(tmp_path, monkeypatch, capsys):
    from tests.test_storage_restore_processes import crash_restore
    location, saved = saved_store(tmp_path)
    database = location.database_path
    contents = (artifact(database, saved['backup_id']) / 'database.sqlite').read_bytes()
    database.write_bytes(contents)
    crash_restore(location, saved['backup_id'], 'prepared')
    monkeypatch.setattr('builtins.input', lambda prompt: saved['backup_id'])
    assert invoke(location, saved['backup_id']) == 0
    result = final_result(capsys)
    assert (maintenance(database) / result['operation_id'] / 'original' / database.name).read_bytes() == contents
    assert database.read_bytes() == contents


def test_resume_requires_fresh_confirmation_and_missing_source_can_still_finish(tmp_path, monkeypatch, capsys):
    from tests.test_storage_restore_processes import crash_restore
    location, saved = saved_store(tmp_path)
    database = location.database_path
    crash_restore(location, saved['backup_id'], 'prepared')
    marker = maintenance(database) / 'restore.json'
    before, target = marker.read_bytes(), database.read_bytes()
    database.with_name(database.name + '.backups').rename(tmp_path / 'offline-backups')
    monkeypatch.setattr('builtins.input', lambda prompt: 'no')
    assert invoke(location, saved['backup_id']) == 1
    assert final_result(capsys)['error'] == 'restore_cancelled'
    assert marker.read_bytes() == before and database.read_bytes() == target
    monkeypatch.setattr('builtins.input', lambda prompt: saved['backup_id'])
    assert invoke(location, saved['backup_id']) == 0
    assert final_result(capsys)['operation_id'] == json.loads(before)['operation_id']


def test_higher_manifest_migration_target_is_diagnostic_not_a_restore_gate(tmp_path, monkeypatch, capsys):
    location, saved = saved_store(tmp_path)
    manifest = artifact(location.database_path, saved['backup_id']) / 'manifest.json'
    manifest.write_text(json.dumps({**saved, 'target_schema': 999}))
    monkeypatch.setattr('builtins.input', lambda prompt: saved['backup_id'])
    assert invoke(location, saved['backup_id']) == 0
    assert final_result(capsys)['status'] == 'completed'


def test_restore_help_has_no_confirmation_bypass(tmp_path, capsys):
    from naiyue_test_agent.cli import main
    with pytest.raises(SystemExit) as caught:
        main(['storage', 'restore', '--help'])
    assert caught.value.code == 0
    assert 'backup_id' in capsys.readouterr().out
    with pytest.raises(SystemExit) as caught:
        main(['storage', 'restore', uuid4().hex, '--yes'])
    assert caught.value.code == 2
