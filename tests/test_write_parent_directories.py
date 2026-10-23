"""Only authorized writes create directories, retaining target and commit guards."""

from native_application_helpers import async_test

import logging
import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.tools import WriteSettings, create_session_write_tool, create_session_edit_tool, create_session_read_tool
from naiyue_test_agent.tools import write as write_module
from naiyue_test_agent.tools.paths import prepare_file_subject
from naiyue_test_agent.tools.permissions import FilePermission
from test_write_tool import make_app, call, results


@async_test
async def test_write_creates_nested_parents_only_after_approval(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    target = tmp_path / 'reports' / '2026' / 'result.md'
    app, model = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': str(target), 'content': 'ok'}, 'write')]), AIMessage(content='done')])
    sid = await app.sessions.create_workspace('A', root)
    paused = await app.runs.start(sid, [('user', 'write the report')])
    assert paused['approval_requests'][0]['target'] == str(target)
    assert not target.parent.parent.exists() and results(paused) == []
    done = await app.runs.resume_approval(sid, ['approve'])
    assert target.read_bytes() == b'ok'
    assert [(m.tool_call_id, m.status, m.content) for m in results(done)] == [('write', 'success', 'write created 2 bytes')]
    assert done['messages'][-1].content == 'done' and len(model.seen) == 2


@pytest.mark.parametrize('decision', ['reject', 'deny'])
@async_test
async def test_rejected_or_denied_write_creates_no_parent(monkeypatch, tmp_path, decision):
    root = tmp_path / 'workspace'
    root.mkdir()
    target = tmp_path / 'new' / 'nested' / 'file.txt'
    options = {'deny': [{'tool': 'write', 'path': str(target)}]} if decision == 'deny' else {}
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': str(target), 'content': 'PRIVATE_CONTENT'}, 'write')]), AIMessage(content='done')], permissions=options)
    sid = await app.sessions.create_workspace('A', root)
    result = await app.runs.start(sid, [('user', 'write')])
    if decision == 'reject':
        assert result['approval_requests'] and not target.parent.parent.exists()
        result = await app.runs.resume_approval(sid, ['reject'])
    assert results(result)[0].status == 'error' and not target.parent.parent.exists()
    assert 'PRIVATE_CONTENT' not in results(result)[0].content


@pytest.mark.parametrize('content,budget,words', [('中文', 4, 'size'), ('\ud800', 10, 'UTF-8')])
@async_test
async def test_invalid_input_never_creates_parent(monkeypatch, tmp_path, content, budget, words):
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': 'new/leaf/file.txt', 'content': content}, 'write')]), AIMessage(content='done')],
        tools=[create_session_write_tool(settings=WriteSettings(max_input_bytes=budget))])
    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'write')])
    assert results(result)[0].status == 'error' and words in results(result)[0].content
    assert not (tmp_path / 'new').exists()


@async_test
async def test_partial_mkdir_failure_keeps_created_parent_and_reports_side_effect(monkeypatch, tmp_path, caplog):
    target = tmp_path / 'new' / 'leaf' / 'file.txt'
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': str(target), 'content': 'PRIVATE_CONTENT'}, 'write')]), AIMessage(content='inspect')])
    original = Path.mkdir
    failure = PermissionError('PRIVATE_MKDIR_DETAIL')
    def mkdir(path, *args, **kwargs):
        if path == target.parent:
            raise failure
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    with caplog.at_level(logging.WARNING):
        result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'write')])
    assert (tmp_path / 'new').is_dir() and not target.parent.exists()
    feedback = results(result)[0]
    assert feedback.status == 'error' and 'parent directories may already have been created' in feedback.content
    assert 'file was not written' in feedback.content and 'PermissionError' in caplog.text
    assert 'PRIVATE' not in feedback.content + caplog.text
    assert not list(tmp_path.rglob('.naiyue-write-*'))


@pytest.mark.parametrize('phase', ['waiting', 'mkdir', 'commit'])
@async_test
async def test_changed_link_never_receives_write_and_keeps_the_link(monkeypatch, tmp_path, phase):
    root, first, second = tmp_path / 'workspace', tmp_path / 'first', tmp_path / 'second'
    for directory in (root, first, second):
        directory.mkdir()
    link = root / 'linked'
    link.symlink_to(first, target_is_directory=True)
    target = first / 'new' / 'leaf' / 'file.txt'
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': str(link / 'new' / 'leaf' / 'file.txt'), 'content': 'PRIVATE_CONTENT'}, 'write')]), AIMessage(content='done')])
    sid = await app.sessions.create_workspace('A', root)
    paused = await app.runs.start(sid, [('user', 'write')])
    assert paused['approval_requests'] and not (first / 'new').exists()
    def redirect():
        link.unlink()
        link.symlink_to(second, target_is_directory=True)
    if phase == 'waiting':
        redirect()
    elif phase == 'mkdir':
        original = Path.mkdir
        def mkdir(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == first / 'new':
                redirect()
            return result
        monkeypatch.setattr(Path, 'mkdir', mkdir)
    else:
        original = write_module.os.fsync
        def fsync(fd):
            result = original(fd)
            redirect()
            return result
        monkeypatch.setattr(write_module.os, 'fsync', fsync)
    result = await app.runs.resume_approval(sid, ['approve'])
    feedback = results(result)[0]
    assert feedback.status == 'error' and ('changed' in feedback.content or 'parent' in feedback.content)
    assert not target.exists() and list(second.iterdir()) == [] and link.is_symlink()
    assert not list(first.rglob('.naiyue-write-*'))
    if phase == 'waiting':
        assert list(first.iterdir()) == []


@async_test
async def test_parent_link_to_authorized_real_target_is_preserved(monkeypatch, tmp_path):
    root, outside = tmp_path / 'workspace', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    link = root / 'linked'
    link.symlink_to(outside, target_is_directory=True)
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': str(link / 'new' / 'file.txt'), 'content': 'ok'}, 'write')]), AIMessage(content='done')])
    sid = await app.sessions.create_workspace('A', root)
    await app.runs.start(sid, [('user', 'write')])
    result = await app.runs.resume_approval(sid, ['approve'])
    assert results(result)[0].status == 'success' and link.is_symlink()
    assert (outside / 'new' / 'file.txt').read_bytes() == b'ok'


@async_test
async def test_edit_still_requires_existing_parent_and_never_creates_one(monkeypatch, tmp_path):
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('edit', {'path': 'new/leaf/file.txt', 'edits': [{'oldText': 'old', 'newText': 'new'}]}, 'edit')]), AIMessage(content='done')],
        tools=[create_session_edit_tool()])
    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'edit')])
    assert results(result)[0].status == 'error' and not (tmp_path / 'new').exists()
    with pytest.raises(ValueError):
        write_module._resolve_write_target(tmp_path, 'new/file.txt')


@async_test
async def test_two_sessions_conflict_before_either_can_create_the_same_parent(monkeypatch, tmp_path):
    entered, release = Event(), Event()
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': 'new/file.txt', 'content': 'FIRST'}, 'first')]),
        AIMessage(content='', tool_calls=[call('write', {'path': 'new/file.txt', 'content': 'SECOND'}, 'second')]),
        AIMessage(content='second done'), AIMessage(content='first done')])
    first_sid, second_sid = [await app.sessions.create_workspace('A', tmp_path) for _ in range(2)]
    original, created = Path.mkdir, []
    def mkdir(path, *args, **kwargs):
        if path == tmp_path / 'new':
            created.append(path)
            entered.set()
            assert release.wait(5)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    future = asyncio.create_task(app.runs.start(first_sid, [('user', 'first')]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        second = await app.runs.start(second_sid, [('user', 'second')])
        assert results(second)[0].status == 'error' and 'busy' in results(second)[0].content
        assert created == [tmp_path / 'new'] and not (tmp_path / 'new').exists()
    finally:
        release.set()
        first = await asyncio.wait_for(asyncio.shield(future), 5)
    assert results(first)[0].status == 'success' and (tmp_path / 'new' / 'file.txt').read_text() == 'FIRST'


@async_test
async def test_write_and_read_same_batch_work_with_a_prepared_missing_parent(monkeypatch, tmp_path):
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': 'new/file.txt', 'content': 'written'}, 'write'),
        call('read', {'path': 'new/file.txt'}, 'read')]), AIMessage(content='done')],
        tools=[create_session_write_tool(), create_session_read_tool()])
    result = await app.runs.start(await app.sessions.create_workspace('A', tmp_path), [('user', 'write then read')])
    assert [(m.tool_call_id, m.status, m.content) for m in results(result)] == [
        ('write', 'success', 'write created 7 bytes'), ('read', 'success', 'written')]


@async_test
async def test_replacing_anchor_during_mkdir_invalidates_the_saved_identity(monkeypatch, tmp_path):
    root, previous = tmp_path / 'workspace', tmp_path / 'previous'
    root.mkdir()
    app, _ = await make_app(monkeypatch, [AIMessage(content='', tool_calls=[
        call('write', {'path': 'new/leaf/file.txt', 'content': 'PRIVATE_CONTENT'}, 'write')]), AIMessage(content='done')])
    sid = await app.sessions.create_workspace('A', root)
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == root / 'new':
            root.rename(previous)
            original(root)
        return result
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    result = await app.runs.start(sid, [('user', 'write')])
    assert results(result)[0].status == 'error' and 'changed' in results(result)[0].content
    assert list(root.iterdir()) == [] and (previous / 'new').is_dir()
    assert not (previous / 'new' / 'leaf').exists()


def test_failed_temp_preparation_keeps_created_directories_without_temp_leak(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError('PRIVATE_TEMP_FAILURE')
    monkeypatch.setattr(write_module.tempfile, 'NamedTemporaryFile', fail)
    with pytest.raises(ToolException, match='I/O'):
        write_module._write(tmp_path, 'new/leaf/file.txt', 'new', 1024,
            subject=prepare_file_subject('new/leaf/file.txt', tmp_path, 'write'))
    assert (tmp_path / 'new' / 'leaf').is_dir() and not list(tmp_path.rglob('.naiyue-write-*'))
    assert not (tmp_path / 'new' / 'leaf' / 'file.txt').exists()


@async_test
async def test_auto_allow_creates_parents_without_repeating_permission_or_review(monkeypatch, tmp_path):
    from test_auto_permission_pipeline import build as auto_app
    from test_auto_permission_review import model, verdict
    from test_dynamic_tools import DynamicModel
    root = tmp_path / 'workspace'
    root.mkdir()
    target = tmp_path / 'outside' / 'new' / 'file.txt'
    prepares = []
    class Counting(FilePermission):
        def prepare(self, args, context):
            prepares.append(True)
            return super().prepare(args, context)
    spec = replace(create_session_write_tool(), permissions=Counting('path', 'write'))
    fast, detailed = model([verdict()]), model([])
    primary = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        call('write', {'path': str(target), 'content': 'allowed'}, 'write')]), AIMessage(content='done')])
    app = await auto_app(monkeypatch, primary, fast, detailed, specs=[spec])
    result = await app.runs.start(await app.sessions.create_workspace('main', root), [('user', 'write')])
    assert results(result)[0].status == 'success' and target.read_text() == 'allowed'
    assert not result.get('approval_requests') and prepares == [True]
    assert len(fast.seen) == 1 and detailed.seen == [] and len(primary.seen) == 2
