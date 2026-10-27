"""Installed commands and private resources work independently of a checkout."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_nyt_help_and_version_need_no_models_or_business_imports(capsys):
    from naiyue_test_agent import __version__, cli
    with pytest.raises(SystemExit) as help_exit:
        cli.main(['--help'])
    assert help_exit.value.code == 0
    assert 'chat' in capsys.readouterr().out
    with pytest.raises(SystemExit) as version_exit:
        cli.main(['--version'])
    assert version_exit.value.code == 0
    assert capsys.readouterr().out.strip() == f'nyt {__version__}'


@pytest.mark.parametrize(('argv', 'module', 'forwarded'), [
    ([], 'naiyue_test_agent.web.__main__', []),
    (['--workspace', 'work'], 'naiyue_test_agent.web.__main__', ['--workspace', 'work']),
    (['chat', '--resume', 'sid'], 'naiyue_test_agent.cli.chat', ['--resume', 'sid']),
])
def test_nyt_routes_to_the_existing_entry(monkeypatch, argv, module, forwarded):
    from naiyue_test_agent import cli
    calls = []
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(main=lambda args: calls.append(args) or 17))
    assert cli.main(argv) == 17
    assert calls == [forwarded]


def test_nyt_rejects_unknown_command(capsys):
    from naiyue_test_agent import cli
    with pytest.raises(SystemExit) as error:
        cli.main(['unsupported'])
    assert error.value.code == 2
    assert 'unsupported' in capsys.readouterr().err


def test_packaged_frontend_and_binaries_take_precedence_over_path(monkeypatch, tmp_path):
    from naiyue_test_agent import resources
    assets = tmp_path / '_assets'
    (assets / 'web').mkdir(parents=True)
    (assets / 'bin').mkdir()
    (assets / 'web' / 'index.html').write_text('packaged frontend')
    (assets / 'native.json').write_text(json.dumps({'target': 'fixture'}))
    executable = assets / 'bin' / ('rg.exe' if os.name == 'nt' else 'rg')
    executable.write_text('fixture')
    executable.chmod(0o755)
    monkeypatch.setattr(resources, 'ASSETS', assets)
    monkeypatch.setattr(resources.shutil, 'which', lambda _: pytest.fail('Packaged tools used PATH'))
    assert resources.frontend_directory() == assets / 'web'
    assert resources.resolve_tool_binary('rg') == str(executable)


def test_incomplete_install_does_not_fall_back_to_system_tools(monkeypatch, tmp_path):
    from naiyue_test_agent import resources
    (tmp_path / 'native.json').write_text('{}')
    monkeypatch.setattr(resources, 'ASSETS', tmp_path)
    monkeypatch.setattr(resources.shutil, 'which', lambda _: pytest.fail('Incomplete install used PATH'))
    with pytest.raises(resources.PackageResourceError, match='reinstall'):
        resources.resolve_tool_binary('fd')
    with pytest.raises(resources.PackageResourceError, match='reinstall'):
        resources.frontend_directory()


def test_source_development_still_resolves_path_tools(monkeypatch, tmp_path):
    from naiyue_test_agent import resources
    monkeypatch.setattr(resources, 'ASSETS', tmp_path / 'absent')
    calls = []
    def which(name):
        calls.append(name)
        return '/fixture/fdfind' if name == 'fdfind' else None
    monkeypatch.setattr(resources.shutil, 'which', which)
    assert resources.resolve_tool_binary('fd') == '/fixture/fdfind'
    assert calls == ['fd', 'fdfind']
    assert resources.resolve_tool_binary('rg') is None
    assert resources.frontend_directory().name == 'dist'


def test_resource_names_are_not_arbitrary_paths():
    from naiyue_test_agent import resources
    with pytest.raises(ValueError, match='Unsupported'):
        resources.resolve_tool_binary('../untrusted')


def test_terminal_missing_model_config_has_setup_guidance(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.cli import chat as cli
    missing = tmp_path / 'missing-models.json'
    assert cli.main(['--config', str(missing), '--workspace', str(tmp_path), '--no-session']) == 1
    output = capsys.readouterr().out
    assert str(missing) in output and '--config' in output and 'README' in output


def test_missing_config_does_not_hide_a_storage_failure(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.cli import chat as cli
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.workspaces import prepare_workspace
    db = tmp_path / 'occupied.sqlite'
    location = prepare_workspace(tmp_path, data_dir=tmp_path / 'storage', database_path=db)
    with ProjectStore(db) as owned:
        owned.sessions.bind_workspace(location.workspace_id, location.root)
        assert cli.main(['--config', str(tmp_path / 'missing'), '--workspace', str(tmp_path),
                         '--session-db', str(db)]) == 1
    output = capsys.readouterr().out
    assert '数据库已被其他服务占用' in output
    assert '未找到模型配置' not in output


def test_partial_asset_directory_does_not_use_path(monkeypatch, tmp_path):
    from naiyue_test_agent import resources
    monkeypatch.setattr(resources, 'ASSETS', tmp_path)
    monkeypatch.setattr(resources.shutil, 'which', lambda _: pytest.fail('Partial install used PATH'))
    with pytest.raises(resources.PackageResourceError, match='reinstall'):
        resources.resolve_tool_binary('fd')


def test_web_default_uses_resource_directory(monkeypatch, tmp_path):
    from naiyue_test_agent.web import __main__ as entry
    (tmp_path / 'index.html').write_text('<html>packaged</html>')
    seen = []
    monkeypatch.setattr(entry, 'get_data_dir', lambda _: tmp_path / 'data')
    monkeypatch.setattr(entry, 'frontend_directory', lambda: tmp_path)
    async def connect(options):
        seen.append(options.frontend_dist)
        raise KeyboardInterrupt()
    monkeypatch.setattr(entry, 'ensure_service', connect)
    assert entry.main(['--config', 'unused', '--workspace', str(tmp_path)]) == 130
    assert seen == [tmp_path]


def test_real_web_startup_missing_models_is_lazy_and_page_can_report_it(tmp_path, monkeypatch, capsys):
    from naiyue_test_agent.web import __main__ as entry
    from web_service_helpers import isolated_web_launch
    from naiyue_test_agent.settings import AppSettings
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'index.html').write_text('<html>local startup fixture</html>')
    missing = tmp_path / 'missing-models.json'
    monkeypatch.setattr(entry, 'load_settings', lambda *a: AppSettings.model_validate(
        {'storage': {'data_dir': tmp_path / 'data'}}))
    observed = []
    async def observe(client, server):
        from naiyue_test_agent.service.settings import ServiceError
        projects = await client.request('GET', '/api/v1/projects')
        scope = projects['items'][0]['project_id']
        with pytest.raises(ServiceError, match='model_unavailable'):
            await client.request('GET', f'/api/v1/scopes/{scope}/models')
        observed.append(True)
    with isolated_web_launch(monkeypatch, observe=observe):
        assert entry.main(['--config', str(missing), '--workspace', str(tmp_path),
            '--frontend-dist', str(assets), '--trust-project']) == 0
    assert observed == [True] and '本地访问链接' in capsys.readouterr().out
