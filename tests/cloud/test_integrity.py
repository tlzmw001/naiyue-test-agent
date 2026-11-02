"""Cross-entry and failure evidence beyond repository happy paths."""
import ast
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CreateProject
from cloud_helpers import settings
from test_history_ingress import setup_task, batch


@pytest.mark.parametrize('after_commit', [False, True])
def test_commit_failure_keeps_original_id_reconcilable(tmp_path, monkeypatch, after_commit):
    original = sqlite3.connect
    class FaultConnection(sqlite3.Connection):
        armed = False
        def commit(self):
            if self.armed:
                self.armed = False
                if after_commit: super().commit()
                raise sqlite3.OperationalError('PRIVATE_COMMIT_DETAILS')
            return super().commit()
    def connect(*args, **kwargs):
        return original(*args, **(kwargs | {'factory': FaultConnection}))
    monkeypatch.setattr(sqlite3, 'connect', connect)
    config = settings(tmp_path)
    command = CreateProject(command_id=str(uuid4()), name='commit fault')
    with open_cloud_runtime(config) as runtime:
        runtime.database.connection.armed = True
        with pytest.raises(sqlite3.OperationalError): runtime.commands.create_project(command)
        projects = runtime.registry.list_projects()
        assert len(projects) == int(after_commit)
    with open_cloud_runtime(config) as runtime:
        receipt = runtime.commands.create_project(command)
        assert len(runtime.registry.list_projects()) == 1
        if projects: assert receipt.resource_id == projects[0].project_id


def test_history_initialization_crash_reopens_original_journal(tmp_path, monkeypatch):
    config = settings(tmp_path)
    with open_cloud_runtime(config) as runtime:
        task, raw = setup_task(runtime)
        data = batch(task)
        original = runtime.registry.history_status_set
        def fail(task_id, status):
            if status == 'ready': raise sqlite3.OperationalError('PRIVATE_INIT_DETAILS')
            return original(task_id, status)
        monkeypatch.setattr(runtime.registry, 'history_status_set', fail)
        with pytest.raises(sqlite3.Error): runtime.append_events(raw, data)
        assert runtime.registry.history_status(task.task_id) == 'initializing'
    with open_cloud_runtime(config) as runtime:
        assert runtime.append_events(raw, data) == {'epoch': data['epoch'], 'last_seq': 1}
        assert runtime.registry.history_status(task.task_id) == 'ready'


def test_cleanup_failure_preserves_primary_exception_and_releases_owner(tmp_path, monkeypatch, caplog):
    config = settings(tmp_path)
    primary = RuntimeError('primary-operation')
    with pytest.raises(RuntimeError) as caught:
        with open_cloud_runtime(config) as runtime:
            task, raw = setup_task(runtime)
            runtime.append_events(raw, batch(task))
            history = runtime._histories[task.task_id].database
            original = history.close
            def fail():
                original()
                raise OSError('PRIVATE_CLOSE_DETAILS')
            monkeypatch.setattr(history, 'close', fail)
            raise primary
    assert caught.value is primary
    assert 'cloud_cleanup_secondary_failed' in caplog.text
    assert 'PRIVATE_CLOSE_DETAILS' not in caplog.text
    with open_cloud_runtime(config) as reopened:
        assert reopened.registry.get_task(task.task_id).task_id == task.task_id


def test_cloud_entry_never_builds_agent_or_imports_A_execution_contracts(tmp_path, monkeypatch):
    import langchain.agents
    def forbidden(*args, **kwargs): raise AssertionError('Agent construction in S01')
    monkeypatch.setattr(langchain.agents, 'create_agent', forbidden)
    from naiyue_test_agent.service.cloud.app import create_cloud_apps
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        owner, task = create_cloud_apps(runtime)
        runtime.commands.create_project(CreateProject(command_id=str(uuid4()), name='no agent'))
        assert owner is not task
    source = Path(__file__).resolve().parents[2] / 'naiyue_test_agent'
    files = [p for name in ('application/cloud', 'service/cloud', 'storage/cloud') for p in (source / name).glob('*.py')]
    files += [source / 'contracts/cloud_tasks.py', source / 'contracts/cloud_events.py']
    forbidden = ('naiyue_test_agent.envd', 'naiyue_test_agent.execution.sandbox',
        'naiyue_test_agent.application.environments', 'naiyue_test_agent.contracts.git_')
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                     else [n.name for n in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name.startswith(forbidden) for name in names), path
