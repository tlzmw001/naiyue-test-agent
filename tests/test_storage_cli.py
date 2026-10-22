"""Offline maintenance is reachable without opening a target or runtime setup."""

from contextlib import closing
import json
import sqlite3

import pytest

from tests.storage_backup_fixture import artifact, source_store


def test_locator_never_opens_or_creates_target_and_missing_marker_is_empty(tmp_path, monkeypatch):
    from naiyue_test_agent.workspaces import locate_workspace
    root = tmp_path / 'root'
    root.mkdir()
    assert locate_workspace(root, data_dir=tmp_path / 'data') is None
    assert list(root.iterdir()) == []
    with closing(source_store(tmp_path)[0]) as source:
        database = source.path
    database.write_bytes(b'not-a-database')
    def forbidden(*args, **kwargs):
        raise AssertionError('must not read the target database')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    value = locate_workspace(tmp_path / 'workspace', data_dir=tmp_path / 'data', database_path=database)
    assert value.database_path == database
    database.unlink()
    assert locate_workspace(value.root, data_dir=tmp_path / 'data', database_path=database) == value
    assert not database.exists()


def test_cli_lists_valid_backup_when_target_corrupt_and_configuration_unusable(tmp_path, monkeypatch, capsys):
    from naiyue_test_agent.cli import main
    from naiyue_test_agent.storage_backup import create_backup
    from naiyue_test_agent import config_paths
    with closing(source_store(tmp_path)[0]) as source:
        saved = create_backup(source.connection, source.path, target_schema=14)
        database = source.path
    database.write_bytes(b'bad')
    home = tmp_path / 'agent'
    home.mkdir()
    (home / 'settings.toml').write_text('PRIVATE-token malformed [')
    monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: home)
    def forbidden(*args, **kwargs):
        raise AssertionError('runtime configuration must not be loaded')
    from naiyue_test_agent import settings
    monkeypatch.setattr(settings, 'load_settings', forbidden)
    assert main(['storage', 'backups', '--workspace', str(tmp_path / 'workspace'), '--session-db', str(database)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['database_path'] == str(database)
    assert result['backups'][0]['manifest'] == saved
    assert database.read_bytes() == b'bad'


def test_storage_only_settings_and_default_target_selection(tmp_path, monkeypatch, capsys, caplog):
    from naiyue_test_agent.cli import main
    from naiyue_test_agent import config_paths
    from naiyue_test_agent.config_paths import load_storage_settings
    home = tmp_path / 'agent'
    home.mkdir()
    settings = home / 'settings.toml'
    monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: home)
    assert load_storage_settings().data_dir is None
    settings.write_text('[storage]\ndata_dir=' + json.dumps(str(tmp_path / 'data')) + '\n[mcp]\nservers=7\n[network]\nmode=42\n')
    assert load_storage_settings().data_dir == tmp_path / 'data'
    with closing(source_store(tmp_path)[0]):
        pass
    assert main(['storage', 'backups', '--workspace', str(tmp_path / 'workspace')]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['backups'] == []
    assert output['database_path'].startswith(str(tmp_path / 'data'))
    assert not (tmp_path / 'data').exists()
    settings.write_text('[storage]\ndata_dir="relative-PRIVATE-token"')
    assert main(['storage', 'backups', '--workspace', str(tmp_path / 'workspace')]) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'storage_settings_invalid'}
    assert 'PRIVATE-token' not in caplog.text


def test_cli_empty_workspace_help_and_cancel(tmp_path, monkeypatch, capsys):
    from naiyue_test_agent.cli import main
    from naiyue_test_agent import config_paths
    monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: tmp_path / 'no-config')
    assert main(['storage', 'backups', '--workspace', str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {'workspace_id': None, 'database_path': None, 'backups': []}
    assert not list(tmp_path.iterdir())
    with pytest.raises(SystemExit) as caught:
        main(['storage', '--help'])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert 'backups' in help_text and 'restore' in help_text
    from naiyue_test_agent import storage_cli
    def cancel(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(storage_cli, 'locate_workspace', cancel)
    assert main(['storage', 'backups', '--workspace', str(tmp_path)]) == 130


@pytest.mark.parametrize('invalid,error_type', [('marker', 'TypeError'), ('settings', 'RecursionError')])
def test_deeply_nested_location_inputs_fail_with_safe_cli_error(tmp_path, monkeypatch, capsys, caplog, invalid, error_type):
    from naiyue_test_agent.cli import main
    from naiyue_test_agent import config_paths
    monkeypatch.setattr(config_paths, 'get_agent_dir', lambda: tmp_path / 'agent')
    if invalid == 'marker':
        directory = tmp_path / '.naiyue'
        directory.mkdir()
        (directory / 'workspace.json').write_text('[' * 2000 + '0' + ']' * 2000)
    else:
        home = tmp_path / 'agent'
        home.mkdir()
        (home / 'settings.toml').write_text('value=' + '[' * 2000 + '0' + ']' * 2000)
    # Within the marker's 4KiB limit JSON parses, then fails the required object shape.
    assert main(['storage', 'backups', '--workspace', str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out) == {
        'error': 'workspace_unavailable' if invalid == 'marker' else 'storage_settings_invalid'}
    assert f'error_type={error_type}' in caplog.text
