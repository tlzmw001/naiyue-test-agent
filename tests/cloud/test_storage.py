import json
import os
import sqlite3
from pathlib import Path

import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.database import Database
from cloud_helpers import settings


def test_new_root_reopens_same_identity_and_only_cloud_schema(tmp_path):
    config = settings(tmp_path)
    with open_cloud_runtime(config) as runtime:
        instance = runtime.instance_id
        assert runtime.registry.list_projects() == []
        with runtime.database.transaction() as connection:
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'projects', 'tasks', 'commands', 'task_credentials', 'meta'} <= tables
        assert not tables & {'sessions', 'execution_environments', 'git_inputs', 'memories'}
        assert not os.get_inheritable(runtime.owner.fd)
        with pytest.raises(CloudError, match='cloud_in_use'):
            with open_cloud_runtime(config):
                pytest.fail('second owner admitted')
    with open_cloud_runtime(config) as runtime:
        assert runtime.instance_id == instance
        assert json.loads((config.data_dir / 'cloud-root.json').read_text())['status'] == 'ready'
    for path in config.data_dir.rglob('*'):
        if os.name != 'nt':
            assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('kind', ['foreign', 'symlink', 'wide', 'protected'])
def test_root_rejection_does_not_mutate_existing_data(tmp_path, kind):
    config = settings(tmp_path)
    root = config.data_dir
    if kind == 'symlink':
        target = tmp_path / 'target'
        target.mkdir(mode=0o700)
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir(mode=0o700)
    if kind == 'foreign':
        (root / 'sessions.sqlite').write_bytes(b'old data')
    elif kind == 'wide':
        root.chmod(0o755)
    elif kind == 'protected':
        config = config.model_copy(update={'protected_roots': (tmp_path,)})
    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    with pytest.raises(CloudError):
        with open_cloud_runtime(config):
            pytest.fail('unsafe root accepted')
    assert sorted(str(p.relative_to(root)) for p in root.rglob('*')) == before


def test_wrong_database_format_is_rejected_without_migration(tmp_path):
    config = settings(tmp_path)
    with open_cloud_runtime(config):
        pass
    path = config.data_dir / 'cloud.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA application_id=123')
    before = path.read_bytes()
    with pytest.raises(CloudError, match='cloud_invalid_format'):
        with open_cloud_runtime(config):
            pytest.fail('foreign database migrated')
    assert path.read_bytes() == before


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'sidecar'])
def test_database_never_opens_special_files(tmp_path, kind):
    root = tmp_path / 'data'
    root.mkdir(mode=0o700)
    path = root / 'unsafe.sqlite'
    target = root / 'other'
    target.write_bytes(b'keep')
    if kind == 'symlink': path.symlink_to(target)
    elif kind == 'hardlink': os.link(target, path)
    elif kind == 'fifo': os.mkfifo(path, 0o600)
    else:
        path.touch(mode=0o600)
        path.with_name(path.name + '-wal').symlink_to(target)
    with pytest.raises(CloudError):
        Database.open_cloud(path, profile='registry')
    assert target.read_bytes() == b'keep'


def test_initialization_failure_can_reconcile_only_own_marker(tmp_path, monkeypatch):
    config = settings(tmp_path)
    original = Database.open_cloud.__func__
    def fail(cls, path, **kwargs):
        raise OSError('INTERNAL_PRIVATE_PATH')
    monkeypatch.setattr(Database, 'open_cloud', classmethod(fail))
    with pytest.raises(CloudError):
        with open_cloud_runtime(config): pass
    marker = json.loads((config.data_dir / 'cloud-root.json').read_text())
    assert marker['status'] == 'initializing'
    monkeypatch.setattr(Database, 'open_cloud', classmethod(original))
    with open_cloud_runtime(config) as runtime:
        assert runtime.instance_id == marker['instance_id']


def test_ready_root_missing_registry_does_not_reinitialize(tmp_path):
    config = settings(tmp_path)
    with open_cloud_runtime(config): pass
    (config.data_dir / 'cloud.sqlite').unlink()
    with pytest.raises(CloudError, match='cloud_registry_missing'):
        with open_cloud_runtime(config): pytest.fail('missing registry recreated')
    assert not (config.data_dir / 'cloud.sqlite').exists()


def test_ready_root_empty_registry_does_not_reinitialize(tmp_path):
    config = settings(tmp_path)
    with open_cloud_runtime(config): pass
    path = config.data_dir / 'cloud.sqlite'
    path.write_bytes(b'')
    with pytest.raises(CloudError, match='cloud_invalid_format'):
        with open_cloud_runtime(config): pytest.fail('empty registry recreated')
    assert path.read_bytes() == b''


def test_initializing_marker_atomic_replacement_truncates_stale_temporary(tmp_path):
    config = settings(tmp_path)
    with open_cloud_runtime(config): pass
    marker = config.data_dir / 'cloud-root.json'
    value = json.loads(marker.read_text())
    value['status'] = 'initializing'
    marker.write_text(json.dumps(value))
    temporary = config.data_dir / '.cloud-root.next'
    temporary.touch(mode=0o600)
    temporary.write_bytes(b'leftover' * 1000)
    with open_cloud_runtime(config): pass
    assert json.loads(marker.read_text())['status'] == 'ready'


def test_ready_registry_missing_instance_metadata_is_not_repaired_silently(tmp_path):
    config = settings(tmp_path)
    with open_cloud_runtime(config): pass
    with sqlite3.connect(config.data_dir / 'cloud.sqlite') as connection:
        connection.execute("DELETE FROM meta WHERE key='instance_id'")
    with pytest.raises(CloudError, match='cloud_instance_missing'):
        with open_cloud_runtime(config): pytest.fail('metadata recreated')
