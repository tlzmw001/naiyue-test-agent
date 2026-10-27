"""CLI persistence is default; temporary and resume paths are explicit."""

from native_application_helpers import async_test

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.cli import chat as cli
from naiyue_test_agent.model_access import ModelCatalog
from test_application_run_events import ToolModel


def configure(monkeypatch):
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {
        'A': ToolModel(responses=[AIMessage(content='answer')]),
    }))


def test_default_creates_persistent_database(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path))
    lines = iter(['q', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused']) == 0
    from naiyue_test_agent.storage.project_registry import ProjectRegistry
    registry = ProjectRegistry(tmp_path / '.naiyue-test-agent/data/projects.sqlite')
    try:
        project = registry.list()[0]
    finally:
        registry.close()
    assert project.database_path.is_file()
    assert not (tmp_path / '.naiyue/workspace.json').exists()
    assert not (tmp_path / '.naiyue-test-agent/sessions.sqlite').exists()
    assert '持久会话' in capsys.readouterr().out


def test_temporary_does_not_create_session_directory(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path))
    lines = iter(['q', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--no-session']) == 0
    assert not list((tmp_path / '.naiyue-test-agent').rglob('*.sqlite*'))
    assert not (tmp_path / '.naiyue/workspace.json').exists()
    assert 'answer' in capsys.readouterr().out


def test_empty_resume_is_noop_without_model_or_new_database(monkeypatch, tmp_path, capsys):
    def unexpected(*_a):
        raise AssertionError('catalog must not be loaded')
    monkeypatch.setattr(application, 'load_model_catalog', unexpected)
    db = tmp_path / 'missing.sqlite'
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 0
    assert '暂无历史会话' in capsys.readouterr().out
    assert not db.exists()


@pytest.mark.parametrize('extra', [['--resume'], ['--resume', 'id'], ['--session-db', 'file']])
def test_temporary_conflicts_are_rejected_before_storage(extra, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path))
    with pytest.raises(SystemExit) as caught:
        cli.main(['--config', 'unused', '--no-session', *extra])
    assert caught.value.code == 2
    assert not (tmp_path / '.naiyue-test-agent').exists()


def test_resume_non_tty_lists_all_without_loading_model(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    db = tmp_path / 'sessions.sqlite'
    from naiyue_test_agent.workspaces import prepare_workspace
    location = prepare_workspace(tmp_path, data_dir=tmp_path / 'data', database_path=db)
    with ProjectStore(db) as storage:
        storage.sessions.bind_workspace(location.workspace_id, location.root)
        storage.sessions.create('one', {'thread_id': 't1', 'model_id': 'A', 'workspace_root': tmp_path, 'active_tool_names': (), 'permission_mode': 'Default'})
        storage.sessions.create('two', {'thread_id': 't2', 'model_id': 'B', 'workspace_root': tmp_path, 'active_tool_names': (), 'permission_mode': 'Default'})
    def unexpected(*_a):
        raise AssertionError('must not load model catalog for list')
    monkeypatch.setattr(application, 'load_model_catalog', unexpected)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 0
    out = capsys.readouterr().out
    assert 'one' in out and 'two' in out and out.index('two') < out.index('one')
    assert '请使用 --resume' in out
    with ProjectStore(db) as storage:
        assert len(storage.sessions.list_sessions()) == 2


def test_picker_selects_without_creating_new_session(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    db = tmp_path / 'sessions.sqlite'
    from naiyue_test_agent.workspaces import prepare_workspace
    location = prepare_workspace(tmp_path, data_dir=tmp_path / 'data', database_path=db)
    with ProjectStore(db) as storage:
        storage.sessions.bind_workspace(location.workspace_id, location.root)
        storage.sessions.create('saved', {'thread_id': 'thread', 'model_id': 'A', 'workspace_root': tmp_path, 'active_tool_names': (), 'permission_mode': 'Default'})
    configure(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    lines = iter(['invalid', '1', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 0
    out = capsys.readouterr().out
    assert '选择无效' in out and '持久会话：saved' in out
    with ProjectStore(db) as storage:
        assert len(storage.sessions.list_sessions()) == 1


def test_direct_resume_continues_same_session(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    db = tmp_path / 'sessions.sqlite'
    configure(monkeypatch)
    lines = iter(['first', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--session-db', str(db)]) == 0
    capsys.readouterr()
    with ProjectStore(db) as storage:
        session = storage.sessions.list_sessions()[0]['session_id']
    lines = iter(['second', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume', session]) == 0
    assert ('持久会话：' + session) in capsys.readouterr().out
    with ProjectStore(db) as storage:
        assert len(storage.sessions.list_sessions()) == 1 and storage.sessions.get(session)['state'] == 'ready'


def test_picker_cancel_does_not_load_model_or_add_record(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    db = tmp_path / 'sessions.sqlite'
    from naiyue_test_agent.workspaces import prepare_workspace
    location = prepare_workspace(tmp_path, data_dir=tmp_path / 'data', database_path=db)
    with ProjectStore(db) as storage:
        storage.sessions.bind_workspace(location.workspace_id, location.root)
        storage.sessions.create('saved', {'thread_id': 'thread', 'model_id': 'A', 'workspace_root': tmp_path, 'active_tool_names': (), 'permission_mode': 'Default'})
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr('builtins.input', lambda _p='': '')
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: (_ for _ in ()).throw(AssertionError()))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 0
    with ProjectStore(db) as storage:
        assert len(storage.sessions.list_sessions()) == 1


def test_model_save_failure_is_safe_and_keeps_previous_choice(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {
        'A': ToolModel(responses=[AIMessage(content='A answer')]),
        'B': ToolModel(responses=[AIMessage(content='B must not run')]),
    }))
    def fail(*_args):
        raise SessionPersistenceError('PRIVATE_SQL_DETAIL')
    from naiyue_test_agent.storage.sessions import SessionRepo
    monkeypatch.setattr(SessionRepo, 'select_model', fail)
    lines = iter([':model B', 'q', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    db = tmp_path / 'sessions.sqlite'
    assert cli.main(['--config', 'unused', '--session-db', str(db)]) == 0
    out = capsys.readouterr().out
    assert '模型选择未保存' in out and 'A answer' in out and 'B must not run' not in out
    assert 'PRIVATE_SQL_DETAIL' not in out
    with ProjectStore(db) as storage:
        assert storage.sessions.list_sessions()[0]['model_id'] == 'A'


def test_broken_database_symlink_is_not_reported_as_empty_history(monkeypatch, tmp_path, capsys):
    db = tmp_path / 'alias.sqlite'
    target = tmp_path / 'missing.sqlite'
    db.symlink_to(target)
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 1
    assert '暂无历史会话' not in capsys.readouterr().out
    assert db.is_symlink() and not target.exists()


def test_restricted_resume_prints_history_without_reading_input_or_running_model(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    configure(monkeypatch)
    db = tmp_path / 'sessions.sqlite'
    lines = iter(['saved request', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--session-db', str(db)]) == 0
    capsys.readouterr()
    with ProjectStore(db) as storage:
        row = storage.sessions.list_sessions()[0]
        sid = row['session_id']
        storage.runs.finish_run(sid, 'running', row['checkpoint_id'], '', None)
        storage.database.connection.execute('UPDATE naiyue_sessions SET active_tool_names_json=? WHERE session_id=?', ('["removed"]', sid))
        storage.database.connection.commit()
        before = tuple(storage.database.connection.iterdump())
    def forbidden(*_a, **_k):
        raise AssertionError('Read-only history must not read input or call a model')
    monkeypatch.setattr('builtins.input', forbidden)
    monkeypatch.setattr(ToolModel, '_astream', forbidden)
    assert cli.main(['--session-db', str(db), '--resume', sid]) == 0
    output = capsys.readouterr().out
    assert '只读历史' in output and 'saved request' in output and 'answer' in output
    assert 'saved_configuration_unavailable' in output and '本次运行完成' not in output
    with ProjectStore(db) as storage:
        assert tuple(storage.database.connection.iterdump()) == before


@async_test
async def test_interactive_auto_recovery_waits_for_new_input_before_running(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    from test_persistent_sessions import build
    configure(monkeypatch)
    db = tmp_path / 'sessions.sqlite'
    lines = iter(['saved request', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--session-db', str(db)]) == 0
    with ProjectStore(db) as storage:
        row = storage.sessions.list_sessions()[0]
        storage.runs.finish_run(row['session_id'], 'failed', row['checkpoint_id'], '', 'failed')
    capsys.readouterr()
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    from terminal_service_helpers import terminal_completed
    finished = terminal_completed(monkeypatch)
    lines = iter(['new request', ':quit'])
    def terminal_input(_p=''):
        line = next(lines)
        if line == ':quit': assert finished.wait(5)
        return line
    monkeypatch.setattr('builtins.input', terminal_input)
    assert cli.main(['--session-db', str(db), '--resume', row['session_id']]) == 0
    output = capsys.readouterr().out
    assert '只读历史' in output and '下一条新消息才会启动运行' in output
    assert output.count('本次运行完成') == 1
    async with build(monkeypatch, db) as app:
        storage = app.host.sessions._persistence.storage
        history = await app.host.history.session(row['session_id'])
        assert [m.content for m in history['messages']] == ['saved request', 'answer', 'new request', 'answer']


def test_open_then_quit_recovers_without_model_or_new_user_message(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.storage.project_store import ProjectStore
    configure(monkeypatch)
    db = tmp_path / 'sessions.sqlite'
    lines = iter(['saved', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--session-db', str(db)]) == 0
    with ProjectStore(db) as storage:
        row = storage.sessions.list_sessions()[0]
        storage.runs.finish_run(row['session_id'], 'failed', row['checkpoint_id'], '', 'failed')
        before = tuple(storage.database.connection.iterdump())
    capsys.readouterr()
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr('builtins.input', lambda _p='': ':quit')
    assert cli.main(['--session-db', str(db), '--resume', row['session_id']]) == 0
    assert '本次运行完成' not in capsys.readouterr().out
    with ProjectStore(db) as storage:
        saved = storage.sessions.get(row['session_id'])
        assert saved['state'] == 'ready' and saved['last_run_status'] == 'recovered'
        assert saved['checkpoint_id'] != row['checkpoint_id']


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_cli_cold_approval_has_no_extra_restore_prompt(monkeypatch, tmp_path, capsys, decision):
    from naiyue_test_agent.storage.project_store import ProjectStore
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'external'
    outside.write_text('external input')
    db = tmp_path / 'sessions.sqlite'
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'external-call'}])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    lines = iter(['read external', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    assert cli.main(['--workspace', str(root), '--session-db', str(db)]) == 0
    with ProjectStore(db) as storage:
        sid = storage.sessions.list_sessions()[0]['session_id']
    capsys.readouterr()
    model = ToolModel(responses=[AIMessage(content='after approval')])
    lines = iter([':review ' + decision, ':quit'])
    assert cli.main(['--workspace', str(root), '--session-db', str(db), '--resume', sid]) == 0
    output = capsys.readouterr().out
    assert '已恢复待审批批次' in output and 'after approval' in output
    assert '输入 continue' not in output
    with ProjectStore(db) as storage:
        assert storage.sessions.get(sid)['state'] == 'ready'


@pytest.mark.parametrize('choice', ['continue', ''])
def test_cli_changed_approval_requires_explicit_choice_to_abandon(monkeypatch, tmp_path, capsys, choice):
    from naiyue_test_agent.storage.project_store import ProjectStore
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'external'
    outside.write_text('original')
    db = tmp_path / 'sessions.sqlite'
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'external-call'}])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    lines = iter(['read external', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    options = ['--workspace', str(root), '--session-db', str(db)]
    assert cli.main(options) == 0
    with ProjectStore(db) as storage:
        row = storage.sessions.list_sessions()[0]
    outside.unlink()
    outside.symlink_to(tmp_path / 'other')
    capsys.readouterr()
    configure(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    lines = iter([choice, ':quit'])
    assert cli.main([*options, '--resume', row['session_id']]) == 0
    output = capsys.readouterr().out
    assert '原审批条件已变化' in output and '本次运行完成' not in output
    with ProjectStore(db) as storage:
        assert storage.sessions.get(row['session_id'])['state'] == ('ready' if choice else 'interrupted')
