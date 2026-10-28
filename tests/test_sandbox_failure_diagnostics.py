"""Background failure logs identify ownership without changing recovery decisions."""
import asyncio
from contextlib import nullcontext
import sqlite3
from threading import Event, RLock
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.envd.operations import EnvironmentOperations, OperationControl
from naiyue_test_agent.application.environments.operations import SandboxOperationService


@pytest.mark.parametrize(('save_fails','log_fails'), [(False,False), (True,False), (True,True)])
def test_worker_logs_execution_and_unconfirmed_terminal_save_separately(caplog, monkeypatch, save_fails, log_fails):
    from naiyue_test_agent.envd import operations
    original_log = operations.logger.error
    if log_fails:
        def log(message, *args, **kwargs):
            if message.startswith('sandbox_operation_failure_save_unconfirmed'):
                raise OSError(28, 'PRIVATE_LOG')
            original_log(message, *args, **kwargs)
        monkeypatch.setattr(operations.logger, 'error', log)
    worker = object.__new__(EnvironmentOperations)
    worker.environment = EnvironmentRef('binding-1', 'sandbox', 'project-1', '/workspace', 'linux')
    worker.session_id = 'session-1'
    worker._lock, worker._active = RLock(), {'operation-1': object()}
    worker._result_pending = False
    original = PermissionError(13, 'PRIVATE_PATH')
    save_error = sqlite3.OperationalError('PRIVATE_DATABASE')
    save_error.sqlite_errorcode, save_error.sqlite_errorname = 14, 'SQLITE_CANTOPEN'
    saved = []
    def dispatch(*args, **kwargs):
        raise original
    def finish(*args):
        if save_fails:
            raise save_error
        saved.append(args)
    worker.files, worker.journal = SimpleNamespace(dispatch=dispatch), SimpleNamespace(finish=finish)
    done = Event()
    if save_fails:
        with pytest.raises(sqlite3.OperationalError) as caught:
            worker._execute('operation-1', 'write', {}, OperationControl(), done)
        assert caught.value is save_error
        if log_fails:
            assert 'diagnostic_log_failed' in caplog.text
        else:
            assert 'sandbox_operation_failure_save_unconfirmed' in caplog.text
            assert 'SQLITE_CANTOPEN' in caplog.text
    else:
        worker._execute('operation-1', 'write', {}, OperationControl(), done)
        assert saved[0][1] == 'unknown' and saved[0][2]['error']['code'] == 'env_operation_unknown'
    assert done.is_set() and not worker._active
    assert 'operation-1' in caplog.text and 'binding-1' in caplog.text
    assert 'execute' in caplog.text and '13' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_collector_exit_is_identifiable_without_swallowing_task_state(caplog):
    service = object.__new__(SandboxOperationService)
    service._closing, service._watchers = False, {}
    def refresh(*args):
        raise sqlite3.OperationalError('PRIVATE_DATABASE')
    service.refresh = refresh
    async def scenario():
        service.watch('binding-1', 'operation-1', 100)
        task = service._watchers[('binding-1', 'operation-1')]
        await task
        assert task.done() and task.exception() is None
    asyncio.run(scenario())
    assert 'collector_stopped' in caplog.text and 'operation-1' in caplog.text
    assert 'binding-1' in caplog.text and 'PRIVATE' not in caplog.text


def test_archive_failure_keeps_unknown_and_stop_behavior_and_logs_phase(caplog):
    service = object.__new__(SandboxOperationService)
    row = dict(project_id='project-1', operation_id='operation-1', kind='shell', state='running',
        observed_bytes=10, metadata={'offset': 0, 'saved_bytes': 0})
    saved, calls = [], []
    service._lock = lambda _: nullcontext()
    service._archive_activity = lambda _: nullcontext()
    service.records = lambda: SimpleNamespace(get=lambda *_: row, save=lambda *args: saved.append(args))
    service._merge = lambda current, remote: current
    service.finished = lambda _: False
    def rpc(*args, **kwargs):
        calls.append(args[1]); return {}
    service._rpc = rpc
    def prepare(*args):
        raise PermissionError(13, 'PRIVATE_ARCHIVE')
    service._archive = lambda _: SimpleNamespace(prepare=prepare)
    with pytest.raises(ToolException, match='Sandbox archive failed'):
        service._refresh('binding-1', 'operation-1')
    assert saved[0][2]['state'] == 'unknown' and calls == ['operation_status', 'operation_stop']
    assert 'archive_prepare' in caplog.text and 'operation-1' in caplog.text
    assert '13' in caplog.text and 'PRIVATE' not in caplog.text


def test_environment_recovery_keeps_failure_identity_and_records_scope(caplog):
    from naiyue_test_agent.application.environments.service import EnvironmentService
    service = object.__new__(EnvironmentService)
    row = SimpleNamespace(binding_id='binding-1', session_id='session-1', project_id='project-1', lifecycle='running')
    saved = []
    row.changed = lambda **values: (saved.append(values), row)[1]
    service._lock = lambda _: nullcontext()
    service.get = lambda _: row
    service.records = lambda: SimpleNamespace(save=lambda _: None)
    original = PermissionError(13, 'PRIVATE_ERROR')
    def action(*args):
        raise original
    service._engine = lambda _: SimpleNamespace(action=action)
    with pytest.raises(PermissionError) as caught:
        service._perform_action('binding-1', 'recover')
    assert caught.value is original and saved[-1]['lifecycle'] == 'unknown'
    assert 'binding-1' in caplog.text and 'session-1' in caplog.text and '"errno":13' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_shutdown_logs_the_failed_container_and_preserves_first_error(caplog):
    from naiyue_test_agent.application.environments.lifecycle import EnvironmentLifecycle
    lifecycle = object.__new__(EnvironmentLifecycle)
    row = SimpleNamespace(binding_id='binding-1', session_id='session-1', lifecycle='running')
    original = PermissionError(13, 'PRIVATE_ERROR')
    def action(*args, **kwargs):
        raise original
    service = SimpleNamespace(activity=SimpleNamespace(manage=lambda _: nullcontext()), _perform_action=action)
    lifecycle.api = SimpleNamespace(_service=service)
    lifecycle.rows = lambda: [row]
    with pytest.raises(PermissionError) as caught:
        asyncio.run(lifecycle.finish_close())
    assert caught.value is original
    assert 'binding-1' in caplog.text and 'session-1' in caplog.text and '"errno":13' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_stop_and_status_failure_keep_original_operation_and_safe_identity(caplog):
    from naiyue_test_agent.contracts.sandbox import SandboxError
    service = object.__new__(SandboxOperationService)
    service.activity = SimpleNamespace(use=lambda _: nullcontext())
    service._lock = lambda _: nullcontext()
    row = dict(state='running', kind='shell')
    def save(*args):
        row.update(args[-1]); return row
    service.records = lambda: SimpleNamespace(get=lambda *_: row, save=save)
    calls = []
    def rpc(binding, operation, params, **kwargs):
        calls.append((binding, operation, params['operation_id']))
        raise SandboxError('env_unreachable')
    service._rpc = rpc
    with pytest.raises(SandboxError, match='env_stop_unconfirmed'):
        service.stop('binding-1', 'operation-1')
    assert calls == [('binding-1','operation_stop','operation-1'), ('binding-1','operation_status','operation-1')]
    assert row['state'] == 'unknown' and row['stale']
    assert 'operation-1' in caplog.text and 'binding-1' in caplog.text and 'stop_status' in caplog.text
