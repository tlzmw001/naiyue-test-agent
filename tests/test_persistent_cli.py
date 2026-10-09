"""CLI persistence is default; temporary and resume paths are explicit."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from naiyue_test_agent import application
from naiyue_test_agent.application import cli
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
    assert (tmp_path / '.naiyue-test-agent' / 'sessions.sqlite').is_file()
    assert '持久会话' in capsys.readouterr().out


def test_temporary_does_not_create_session_directory(monkeypatch, tmp_path, capsys):
    configure(monkeypatch)
    monkeypatch.setattr(Path, 'home', classmethod(lambda _cls: tmp_path))
    lines = iter(['q', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--no-session']) == 0
    assert not (tmp_path / '.naiyue-test-agent').exists()
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
    from naiyue_test_agent.application.session_storage import SessionStorage
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db) as storage:
        storage.create('one', {'thread_id': 't1', 'model_id': 'A', 'workspace_root': None})
        storage.create('two', {'thread_id': 't2', 'model_id': 'B', 'workspace_root': tmp_path})
    def unexpected(*_a):
        raise AssertionError('must not load model catalog for list')
    monkeypatch.setattr(application, 'load_model_catalog', unexpected)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 0
    out = capsys.readouterr().out
    assert 'one' in out and 'two' in out and out.index('two') < out.index('one')
    assert '请使用 --resume' in out
    with SessionStorage(db) as storage:
        assert len(storage.list_sessions()) == 2


def test_picker_selects_without_creating_new_session(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.application.session_storage import SessionStorage
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db) as storage:
        storage.create('saved', {'thread_id': 'thread', 'model_id': 'A', 'workspace_root': None})
    configure(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    lines = iter(['invalid', '1', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 0
    out = capsys.readouterr().out
    assert '选择无效' in out and '持久会话：saved' in out
    with SessionStorage(db) as storage:
        assert len(storage.list_sessions()) == 1


def test_direct_resume_continues_same_session(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.application.session_storage import SessionStorage
    db = tmp_path / 'sessions.sqlite'
    configure(monkeypatch)
    lines = iter(['first', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--session-db', str(db)]) == 0
    capsys.readouterr()
    with SessionStorage(db) as storage:
        session = storage.list_sessions()[0]['session_id']
    lines = iter(['second', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume', session]) == 0
    assert ('持久会话：' + session) in capsys.readouterr().out
    with SessionStorage(db) as storage:
        assert len(storage.list_sessions()) == 1 and storage.get(session)['state'] == 'ready'


def test_picker_cancel_does_not_load_model_or_add_record(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.application.session_storage import SessionStorage
    db = tmp_path / 'sessions.sqlite'
    with SessionStorage(db) as storage:
        storage.create('saved', {'thread_id': 'thread', 'model_id': 'A', 'workspace_root': None})
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr('builtins.input', lambda _p='': '')
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: (_ for _ in ()).throw(AssertionError()))
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 0
    with SessionStorage(db) as storage:
        assert len(storage.list_sessions()) == 1


def test_model_save_failure_is_safe_and_keeps_previous_choice(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {
        'A': ToolModel(responses=[AIMessage(content='A answer')]),
        'B': ToolModel(responses=[AIMessage(content='B must not run')]),
    }))
    def fail(*_args):
        raise SessionPersistenceError('PRIVATE_SQL_DETAIL')
    monkeypatch.setattr(SessionStorage, 'select_model', fail)
    lines = iter([':model B', 'q', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _p='': next(lines))
    db = tmp_path / 'sessions.sqlite'
    assert cli.main(['--config', 'unused', '--session-db', str(db)]) == 0
    out = capsys.readouterr().out
    assert '模型选择未保存' in out and 'A answer' in out and 'B must not run' not in out
    assert 'PRIVATE_SQL_DETAIL' not in out
    with SessionStorage(db) as storage:
        assert storage.list_sessions()[0]['model_id'] == 'A'


def test_broken_database_symlink_is_not_reported_as_empty_history(monkeypatch, tmp_path, capsys):
    db = tmp_path / 'alias.sqlite'
    target = tmp_path / 'missing.sqlite'
    db.symlink_to(target)
    assert cli.main(['--config', 'unused', '--session-db', str(db), '--resume']) == 1
    assert '暂无历史会话' not in capsys.readouterr().out
    assert db.is_symlink() and not target.exists()
