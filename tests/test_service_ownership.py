"""Service ownership is an OS lock, independent of discovery-file existence."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from naiyue_test_agent.service.ownership import ServiceOwner, read_discovery, service_running
from naiyue_test_agent.service.settings import ServiceError, ServiceOptions


def options(tmp_path, **changes):
    values = dict(data_dir=tmp_path / 'data', config_path=tmp_path / 'models.json',
        settings_path=tmp_path / 'settings.toml', frontend_dist=tmp_path / 'assets', port=8123)
    values.update(changes)
    return ServiceOptions(**values)


def test_owner_lock_is_exclusive_and_released_without_deleting_lock_file(tmp_path):
    config = options(tmp_path)
    with ServiceOwner(config) as first:
        assert service_running(config)
        assert not os.get_inheritable(first.fd)
        with pytest.raises(ServiceError, match='service_in_use'):
            with ServiceOwner(config):
                pytest.fail('second owner entered')
        assert not (config.data_dir / 'service/discovery.json').exists()
        first.publish()
        record = read_discovery(config)
        assert record.instance_id == first.record.instance_id
        assert record.identity == config.identity
        assert record.access_token not in repr(record)
    assert not service_running(config)
    assert (config.data_dir / 'service/owner.lock').is_file()
    assert read_discovery(config) is None
    with ServiceOwner(config) as second:
        assert second.record.instance_id != record.instance_id


def test_different_data_directories_have_independent_owners(tmp_path):
    a = options(tmp_path)
    b = options(tmp_path, data_dir=tmp_path / 'other')
    with ServiceOwner(a), ServiceOwner(b):
        assert service_running(a) and service_running(b)


def test_canonical_data_alias_uses_the_same_service_lock(tmp_path):
    config = options(tmp_path)
    alias = options(tmp_path, data_dir=config.data_dir / '..' / 'data')
    with ServiceOwner(config):
        with pytest.raises(ServiceError, match='service_in_use'):
            with ServiceOwner(alias):
                pytest.fail('alias acquired the same service twice')


def test_crash_releases_lock_but_stale_discovery_is_not_an_owner(tmp_path):
    config = options(tmp_path)
    script = """
import sys
from naiyue_test_agent.service.settings import ServiceOptions
from naiyue_test_agent.service.ownership import ServiceOwner
config = ServiceOptions.model_validate_json(sys.argv[1])
with ServiceOwner(config) as owner:
    owner.publish()
    print('ready', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen([sys.executable, '-c', script, config.model_dump_json()],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        old = read_discovery(config)
        assert service_running(config)
        process.kill()
        process.wait(timeout=10)
        assert not service_running(config)
        assert read_discovery(config).instance_id == old.instance_id
        with ServiceOwner(config) as replacement:
            replacement.publish()
            assert read_discovery(config).instance_id != old.instance_id
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize('name', ['owner.lock', 'discovery.json'])
def test_service_files_reject_symlinks_without_touching_target(tmp_path, name):
    config = options(tmp_path)
    folder = config.data_dir / 'service'
    folder.mkdir(parents=True)
    target = tmp_path / 'target'
    target.write_text('private sentinel')
    try:
        (folder / name).symlink_to(target)
    except OSError:
        pytest.skip('OS does not permit this test to create symlinks')
    if name == 'owner.lock':
        with pytest.raises(ServiceError, match='service_storage_unavailable'):
            with ServiceOwner(config):
                pytest.fail('unsafe lock accepted')
    else:
        with pytest.raises(ServiceError, match='service_discovery_invalid'):
            read_discovery(config)
    assert target.read_text() == 'private sentinel'


def test_discovery_rejects_non_loopback_and_never_exposes_token(tmp_path):
    config = options(tmp_path)
    with ServiceOwner(config) as owner:
        owner.publish()
        file = config.data_dir / 'service/discovery.json'
        raw = json.loads(file.read_text())
        raw['origin'] = 'http://example.invalid:80'
        file.write_text(json.dumps(raw))
        with pytest.raises(ServiceError, match='service_discovery_invalid') as caught:
            read_discovery(config)
        assert raw['access_token'] not in str(caught.value)


def test_owner_initialization_failure_releases_lock(tmp_path):
    config = options(tmp_path)
    with pytest.raises(RuntimeError, match='startup'):
        with ServiceOwner(config) as owner:
            owner.publish()
            raise RuntimeError('startup')
    assert not service_running(config)
    with ServiceOwner(config):
        pass


def test_record_preparation_failure_releases_lock(tmp_path, monkeypatch):
    from naiyue_test_agent.service import ownership
    config = options(tmp_path)
    def fail(**kwargs):
        raise RuntimeError('record preparation')
    monkeypatch.setattr(ownership, 'ServiceRecord', fail)
    with pytest.raises(RuntimeError, match='record preparation'):
        with ServiceOwner(config):
            pytest.fail('record construction unexpectedly succeeded')
    assert not service_running(config)


def test_unlock_error_does_not_replace_primary_and_handle_is_still_closed(tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.service import ownership
    config = options(tmp_path)
    primary = RuntimeError('primary')
    actual = ownership.release_store_lock
    def fail(fd):
        raise OSError('unlock failure')
    with pytest.raises(RuntimeError) as caught:
        with ServiceOwner(config):
            monkeypatch.setattr(ownership, 'release_store_lock', fail)
            raise primary
    assert caught.value is primary
    monkeypatch.setattr(ownership, 'release_store_lock', actual)
    assert not service_running(config)
    assert 'service_unlock_failed' in caplog.text
