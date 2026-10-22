"""Windows handle/path times may differ, while real changes must still conflict."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from test_edit_tool import call, make_app, tool_result
from naiyue_test_agent.tools import EditSettings, create_session_edit_tool, create_session_read_tool
from naiyue_test_agent.tools import edit as module
from naiyue_test_agent.tools.paths import prepare_file_subject


def changed_stat(info, field, amount=1_000_000_000):
    values = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
    values[field] += amount
    return SimpleNamespace(**values)


@async_test
@pytest.mark.parametrize('fuzzy', [False, True])
async def test_read_then_edit_accepts_stable_windows_handle_and_path_times(monkeypatch, tmp_path, fuzzy):
    target = tmp_path / 'case.md'
    original = 'print(“old”)\r\n' if fuzzy else 'before\n'
    target.write_bytes(original.encode())
    identity = target.stat().st_ino
    fstat = module.os.fstat

    def handle_stat(fd):
        info = fstat(fd)
        return changed_stat(info, 'st_ctime_ns') if info.st_ino == identity else info

    # CPython 3.12 Windows: fstat reports change time; path stat preserves birth time.
    monkeypatch.setattr(module.os, 'fstat', handle_stat)
    app, _ = await make_app(monkeypatch, [
        AIMessage(content='', tool_calls=[
            call('read', {'path': target.name}, 'before'),
            call('edit', {'path': target.name, 'edits': [{
                'oldText': 'print("old")' if fuzzy else 'before',
                'newText': 'print("new")' if fuzzy else 'after',
            }]}, 'edit'),
            call('read', {'path': target.name}, 'after'),
        ]),
        AIMessage(content='done'),
    ], [create_session_read_tool(), create_session_edit_tool()])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'edit')])
    expected = 'print("new")\r\n' if fuzzy else 'after\n'
    assert tool_result(result, 'before').status == 'success'
    if fuzzy:
        assert 'old' in tool_result(result, 'before').content
    else:
        assert tool_result(result, 'before').content == original
    assert tool_result(result, 'edit').status == 'success', tool_result(result, 'edit').content
    assert tool_result(result, 'after').status == 'success'
    if fuzzy:
        assert 'new' in tool_result(result, 'after').content
    else:
        assert tool_result(result, 'after').content == expected
    assert target.read_bytes() == expected.encode()
    assert not list(tmp_path.glob('.naiyue-write-*'))


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'])
def test_edit_rejects_path_metadata_change_after_copy(monkeypatch, tmp_path, field):
    target = tmp_path / 'case.md'
    target.write_bytes(b'old\n')
    path_stat, feedback = Path.stat, module.build_edit_feedback
    changed = False

    def named_stat(path, *args, **kwargs):
        info = path_stat(path, *args, **kwargs)
        return changed_stat(info, field) if path == target and changed else info

    def prepare_feedback(*args, **kwargs):
        nonlocal changed
        result = feedback(*args, **kwargs)
        changed = True
        return result

    monkeypatch.setattr(Path, 'stat', named_stat)
    monkeypatch.setattr(module, 'build_edit_feedback', prepare_feedback)
    with pytest.raises(module.ToolException, match='file changed'):
        module._edit(tmp_path, target.name, [module.EditReplacement(oldText='old', newText='new')],
            EditSettings(), subject=prepare_file_subject(target.name, tmp_path, 'write'))
    assert target.read_bytes() == b'old\n'
    assert not list(tmp_path.glob('.naiyue-write-*'))


def test_edit_still_rejects_handle_change_time_drift(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_bytes(b'old\n')
    identity = target.stat().st_ino
    fstat = module.os.fstat
    calls = 0

    def handle_stat(fd):
        nonlocal calls
        info = fstat(fd)
        if info.st_ino == identity:
            calls += 1
            if calls >= 3:  # Exact scan start/end, then the source is reopened for copying.
                return changed_stat(info, 'st_ctime_ns')
        return info

    monkeypatch.setattr(module.os, 'fstat', handle_stat)
    with pytest.raises(module.ToolException, match='file changed'):
        module._edit(tmp_path, target.name, [module.EditReplacement(oldText='old', newText='new')],
            EditSettings(), subject=prepare_file_subject(target.name, tmp_path, 'write'))
    assert calls >= 3
    assert target.read_bytes() == b'old\n'
    assert not list(tmp_path.glob('.naiyue-write-*'))


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_size'])
def test_edit_rejects_path_handle_identity_mismatch_before_copy(monkeypatch, tmp_path, field):
    target = tmp_path / 'case.md'
    target.write_bytes(b'old\n')
    path_stat, copy = Path.stat, module._copy_to_temp

    def named_stat(path, *args, **kwargs):
        info = path_stat(path, *args, **kwargs)
        return changed_stat(info, field) if path == target else info

    def copy_with_different_named_file(*args, **kwargs):
        monkeypatch.setattr(Path, 'stat', named_stat)
        return copy(*args, **kwargs)

    monkeypatch.setattr(module, '_copy_to_temp', copy_with_different_named_file)
    with pytest.raises(module.ToolException, match='file changed'):
        module._edit(tmp_path, target.name, [module.EditReplacement(oldText='old', newText='new')],
            EditSettings(), subject=prepare_file_subject(target.name, tmp_path, 'write'))
    assert target.read_bytes() == b'old\n'
    assert not list(tmp_path.glob('.naiyue-write-*'))


def test_digest_rejects_same_length_content_with_restored_times_and_stable_reported_stamp(monkeypatch, tmp_path):
    target = tmp_path / 'case.md'
    target.write_bytes(b'old\n')
    subject = prepare_file_subject(target.name, tmp_path, 'write')
    original = target.stat()
    scan, fstat = module._scan_target, module.os.fstat

    def change_after_scan(*args, **kwargs):
        result = scan(*args, **kwargs)
        target.write_bytes(b'ALT\n')
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
        return result

    def stable_reported_stamp(fd):
        info = fstat(fd)
        return original if info.st_ino == original.st_ino else info

    monkeypatch.setattr(module, '_scan_target', change_after_scan)
    monkeypatch.setattr(module.os, 'fstat', stable_reported_stamp)
    with pytest.raises(module.ToolException, match='file changed'):
        module._edit(tmp_path, target.name, [module.EditReplacement(oldText='old', newText='new')],
            EditSettings(), subject=subject)
    assert target.read_bytes() == b'ALT\n'
    assert not list(tmp_path.glob('.naiyue-write-*'))
