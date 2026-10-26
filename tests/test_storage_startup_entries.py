"""Storage startup failures stay ahead of model/MCP setup and reach both terminals."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import traceback
from types import SimpleNamespace

import pytest

from naiyue_test_agent.storage.backup_schema import CURRENT_SCHEMA

from naiyue_test_agent import application
from naiyue_test_agent.storage.errors import SessionPersistenceError
from scripts.component_fixtures import open_component_application
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.model_access import ModelConfigError
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.storage.backup import list_backups
from tests.storage_backup_fixture import closed_store


def test_async_backup_failure_never_reaches_catalog_mcp_or_saver(tmp_path, monkeypatch):
    from naiyue_test_agent.storage import migrations as storage_migrations
    from naiyue_test_agent.storage import resources as storage_resources
    from naiyue_test_agent.mcp_client import resources
    database, _, _ = closed_store(tmp_path, version=12)
    called = []
    def forbidden(*args, **kwargs):
        called.append(True)
        pytest.fail('failure must stop later assembly')
    original = OSError('PRIVATE-io-body')
    def failure(*args, **kwargs):
        raise original
    monkeypatch.setattr(storage_migrations, 'create_backup', failure)
    monkeypatch.setattr(application, 'load_model_catalog', forbidden)
    monkeypatch.setattr(resources, 'MCPSessionResources', forbidden)
    monkeypatch.setattr(storage_resources, 'AsyncSqliteSaver', forbidden)
    async def run():
        with pytest.raises(SessionPersistenceError, match='backup_failed') as caught:
            async with open_component_application('unused', database_path=database, tools=[],
                    system_prompt='', response_format=None, settings=AppSettings()):
                pytest.fail('must not enter the application')
        assert caught.value.__cause__ is original
    asyncio.run(run())
    assert called == []


def test_model_failure_after_migration_stays_model_failure_and_does_not_restore(tmp_path, monkeypatch):
    database, _, _ = closed_store(tmp_path, version=12)
    original = ModelConfigError('PRIVATE-model-configuration')
    def failure(*args, **kwargs):
        raise original
    monkeypatch.setattr(application, 'load_model_catalog', failure)
    async def run():
        with pytest.raises(ModelConfigError) as caught:
            async with open_component_application('unused', database_path=database, tools=[],
                    system_prompt='', response_format=None, settings=AppSettings()):
                pytest.fail('must not enter the application')
        assert caught.value is original
    asyncio.run(run())
    with ProjectStore(database) as storage:
        assert storage.database.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == CURRENT_SCHEMA
    assert [row['manifest']['source_schema'] for row in list_backups(database)] == [12]


def test_chat_reports_structured_migration_failure_without_private_body(tmp_path, monkeypatch, capsys, caplog):
    from naiyue_test_agent.cli import chat as cli
    from naiyue_test_agent.storage.conversation_sql import chat_queries
    database, root, _ = closed_store(tmp_path, version=6)
    monkeypatch.setattr(cli, 'load_settings', lambda: AppSettings())
    def failure(*args, **kwargs):
        raise RuntimeError('PRIVATE-SQL-parameters')
    monkeypatch.setattr(chat_queries, 'migrate_chat_queries', failure)
    assert cli.main(['--workspace', str(root), '--session-db', str(database), '--config', 'unused']) == 1
    output = capsys.readouterr().out
    assert all(value in output for value in ('migration_failed', 'schema_8', '源版本=6', '当前版本=7', f'目标版本={CURRENT_SCHEMA}', 'nyt storage backups'))
    assert list_backups(database)[0]['backup_id'] in output
    assert 'PRIVATE-SQL-parameters' not in output + caplog.text


@pytest.mark.parametrize('stage', ['connect', 'prepare'])
def test_web_terminal_forwards_structured_service_failure(tmp_path, monkeypatch, capsys, caplog, stage):
    from naiyue_test_agent.web import __main__ as entry
    from naiyue_test_agent.storage.errors import StorageStartupError
    from naiyue_test_agent.service.settings import ServiceError, StartupFailure
    from naiyue_test_agent.application.projects.selection import ProjectSelection
    from web_service_helpers import isolated_web_launch
    original = StorageStartupError('migration_failed', database_path=tmp_path / 'db', phase='schema_8',
        source_schema=6, current_schema=7, target_schema=CURRENT_SCHEMA, backup_id='a' * 32)
    original.__cause__ = OSError('PRIVATE-provider-response')
    cause = original.__cause__
    async def failure(*args, **kwargs):
        raise original
    (tmp_path / 'index.html').write_text('<html>fixture</html>')
    monkeypatch.setattr(entry, 'load_settings', lambda *a: AppSettings.model_validate(
        {'storage': {'data_dir': tmp_path / 'data'}}))
    if stage == 'connect':
        facts = StartupFailure(database_path=str(tmp_path / 'db'), phase='schema_8',
            source_schema=6, current_schema=7, target_schema=CURRENT_SCHEMA, backup_id='a'*32)
        async def connect(options):
            raise ServiceError('migration_failed', startup=facts) from None
        monkeypatch.setattr(entry, 'ensure_service', connect)
        assert entry.main(['--workspace', str(tmp_path), '--frontend-dist', str(tmp_path)]) == 1
    else:
        monkeypatch.setattr(ProjectSelection, 'prepare', failure)
        with isolated_web_launch(monkeypatch):
            assert entry.main(['--workspace', str(tmp_path), '--frontend-dist', str(tmp_path)]) == 1
    assert original.__cause__ is cause
    output = capsys.readouterr().out
    assert all(value in output for value in ('migration_failed', '源版本=6', '当前版本=7', 'a' * 32))
    assert 'PRIVATE-provider-response' not in output + caplog.text
