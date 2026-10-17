"""Platform branches prove routing and failure contracts; native Windows is separate."""

import ctypes
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException

from naiyue_test_agent.tools import write as write_module, edit as edit_module
from naiyue_test_agent.tools.paths import prepare_file_subject
from naiyue_test_agent.tools.settings import EditSettings
from naiyue_test_agent.tools.edit import EditReplacement


def windows_io(monkeypatch):
    proxy = SimpleNamespace(**vars(os))
    proxy.name = 'nt'
    proxy.__dict__.pop('fchmod', None)
    monkeypatch.setattr(write_module, 'os', proxy)
    monkeypatch.setattr(edit_module, 'os', proxy)
    return proxy


@pytest.mark.parametrize('operation', ['write', 'edit'])
def test_windows_existing_file_commits_without_posix_fchmod(tmp_path, monkeypatch, operation):
    path = tmp_path / 'file.txt'
    path.write_text('before')
    subject = prepare_file_subject('file.txt', tmp_path, 'write')
    proxy = windows_io(monkeypatch)
    commits = []
    def replace(temporary, target):
        commits.append((temporary, target))
        os.replace(temporary, target)
    monkeypatch.setattr(write_module, '_windows_replace_file', replace, raising=False)
    if operation == 'write':
        result = write_module._write(tmp_path, 'file.txt', 'after', 1024, subject=subject)
    else:
        result = edit_module._edit(tmp_path, 'file.txt', [EditReplacement(oldText='before', newText='after')],
                                   EditSettings(), subject=subject)
    assert len(commits) == 1 and commits[0][1] == path
    assert path.read_text() == 'after'
    assert list(tmp_path.glob('.naiyue-write-*')) == []


def test_windows_new_file_does_not_call_existing_file_replacement(tmp_path, monkeypatch):
    subject = prepare_file_subject('new.txt', tmp_path, 'write')
    windows_io(monkeypatch)
    monkeypatch.setattr(write_module, '_windows_replace_file', lambda *_: pytest.fail('new target'), raising=False)
    assert 'created' in write_module._write(tmp_path, 'new.txt', 'new', 1024, subject=subject)
    assert (tmp_path / 'new.txt').read_text() == 'new'


def test_windows_readonly_target_is_not_replaced_or_made_writable(monkeypatch, tmp_path):
    windows_io(monkeypatch)
    calls = []
    monkeypatch.setattr(write_module, '_windows_replace_file', lambda *args: calls.append(args), raising=False)
    target = SimpleNamespace(stat=lambda: SimpleNamespace(st_file_attributes=1))
    with pytest.raises(ToolException, match='read.only'):
        write_module._replace_temp(tmp_path / 'temp', target, existed=True)
    assert calls == []


def test_windows_commit_failure_has_no_fallback_and_keeps_unknown_feedback(tmp_path, monkeypatch, caplog):
    path = tmp_path / 'file'
    path.write_text('before')
    subject = prepare_file_subject('file', tmp_path, 'write')
    proxy = windows_io(monkeypatch)
    def fail(*_args):
        raise OSError('PRIVATE_SYNTHETIC_FAILURE')
    monkeypatch.setattr(write_module, '_windows_replace_file', fail, raising=False)
    proxy.replace = lambda *_: pytest.fail('must not bypass preservation')
    with pytest.raises(ToolException, match='outcome is unknown'):
        write_module._write(tmp_path, 'file', 'after', 1024, subject=subject)
    assert path.read_text() == 'before' and list(tmp_path.glob('.naiyue-write-*')) == []
    assert 'write_commit_unknown' in caplog.text and 'PRIVATE_SYNTHETIC_FAILURE' not in caplog.text


def test_native_replace_parameters_do_not_ignore_acl_failures(monkeypatch, tmp_path):
    calls = []
    def native(*args):
        calls.append(args)
        return 1
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: SimpleNamespace(ReplaceFileW=native), raising=False)
    write_module._windows_replace_file(tmp_path / 'temporary', tmp_path / 'target')
    assert calls == [(str(tmp_path / 'target'), str(tmp_path / 'temporary'), None, 0, None, None)]


def test_native_replace_failure_raises_system_error(monkeypatch, tmp_path):
    def native(*args):
        return 0
    original = OSError('synthetic_win32_error')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: SimpleNamespace(ReplaceFileW=native), raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', lambda code: original, raising=False)
    with pytest.raises(OSError) as caught:
        write_module._windows_replace_file(tmp_path / 'temporary', tmp_path / 'target')
    assert caught.value is original


def test_windows_target_removed_before_commit_does_not_become_new_file(tmp_path, monkeypatch):
    windows_io(monkeypatch)
    calls = []
    def replace(*args):
        calls.append(args)
        raise FileNotFoundError('synthetic_missing_original')
    monkeypatch.setattr(write_module, '_windows_replace_file', replace)
    with pytest.raises(ToolException, match='outcome is unknown'):
        write_module._replace_temp(tmp_path / 'temp', tmp_path / 'missing', existed=True)
    # stat() fails before the native operation; it never falls back to creating a target.
    assert calls == []
