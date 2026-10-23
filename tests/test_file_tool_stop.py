"""Cooperative stops must leave actual files and owned resources consistent."""

from dataclasses import replace
from runtime_context_helpers import ExecutionRoot, RunIdentity, ToolAuthorization, typed_context
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.tools import ToolException
from PIL import Image

from naiyue_test_agent.agent_runtime.run_control import RunControl
from naiyue_test_agent.tools import (
    ReadSettings, create_session_read_tool, create_session_write_tool, create_session_edit_tool,
)
from naiyue_test_agent.tools import write as write_module, edit as edit_module
from naiyue_test_agent.tools.permissions import execution_environment


def invoke_body(spec, root, args, control):
    """Enter the real tool body with a valid, already reviewed execution permit."""
    from naiyue_test_agent.tools.permission_rules import PermissionSettings
    context = typed_context(identity=RunIdentity(session_id='file-stop', execution_root=ExecutionRoot(root) if root is not None else None), authorization=ToolAuthorization(permission_rules=PermissionSettings(), session_allow=[], permission_mode='Default'), control=control)
    subject = spec.permissions.prepare(args, context)
    subject.update(args=args, path_field='path', execution_environment=execution_environment())
    context = replace(context, authorization=replace(context.authorization, permit={
        'call_id': 'file', 'session_id': 'file-stop', 'agent_path': 'root', 'subject': subject}))
    runtime = SimpleNamespace(context=context, tool_call_id='file')
    validated = spec.validate_input(args)
    return spec.tool.func(**{key: getattr(validated, key) for key in args}, runtime=runtime)


class ObservedStream:
    def __init__(self, source, after):
        self.source, self.after = source, after

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.source.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.source, name)

    def read(self, size=-1):
        result = self.source.read(size)
        self.after('read', size)
        return result

    def readline(self, size=-1):
        result = self.source.readline(size)
        self.after('readline', size)
        return result


@pytest.mark.parametrize('name', ['read', 'write', 'edit'])
def test_pre_requested_stop_never_opens_target_or_temp(monkeypatch, tmp_path, name):
    target = tmp_path / 'file.txt'
    target.write_text('original')
    factories = {'read': create_session_read_tool, 'write': create_session_write_tool,
                 'edit': create_session_edit_tool}
    args = {'path': target.name}
    if name == 'write':
        args['content'] = 'replacement'
    if name == 'edit':
        args['edits'] = [{'oldText': 'original', 'newText': 'replacement'}]
    opened = []
    original = Path.open
    def open_file(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_file)
    control = RunControl()
    control.request()
    with pytest.raises(ToolException, match='stopped'):
        invoke_body(factories[name](), tmp_path, args, control)
    assert opened == []
    with original(target, 'rb') as source:
        assert source.read() == b'original'
    assert not list(tmp_path.glob('.naiyue-write-*'))


@pytest.mark.parametrize('case', ['header', 'small', 'offset', 'long_skip', 'long_prefix', 'page_end'])
def test_read_stops_at_io_boundary_without_returning_a_partial_body(monkeypatch, tmp_path, case):
    target = tmp_path / 'file.txt'
    target.write_text('PRIVATE_CONTENT\n' + ('x' * 400 if case.startswith('long') else 'line\n' * 80) + '\nend')
    args = {'path': target.name}
    if case in ('offset', 'long_skip'):
        args.update(offset=20, limit=1)
    if case == 'long_prefix':
        args.update(offset=2, limit=1)
    if case == 'page_end':
        args.update(offset=1, limit=1)
    control, streams, reads = RunControl(), [], []
    original = Path.open
    def after(method, size):
        assert not control.requested, 'Read continued issuing IO after the stop boundary'
        reads.append((method, size))
        trigger = ((case == 'header' and method == 'read' and size == 32)
                   or (case == 'small' and method == 'read' and size != 32)
                   or (case == 'offset' and method == 'readline')
                   or (case in ('long_skip', 'long_prefix') and method == 'readline' and size > 32 and len(reads) >= 3)
                   or (case == 'page_end' and method == 'read' and size == 1))
        if trigger:
            control.request()
    def open_file(path, mode='r', *args, **kwargs):
        source = original(path, mode, *args, **kwargs)
        if path == target and mode == 'rb':
            streams.append(source)
            return ObservedStream(source, after)
        return source
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(ToolException, match='stopped') as caught:
        invoke_body(create_session_read_tool(settings=ReadSettings(max_output_bytes=64)), tmp_path, args, control)
    assert control.requested and all(s.closed for s in streams)
    assert 'PRIVATE_CONTENT' not in str(caught.value)


@pytest.mark.parametrize('stage', ['decode', 'resize', 'encode'])
def test_image_stops_between_pillow_stages_without_image_blocks(monkeypatch, tmp_path, stage):
    target = tmp_path / 'image.png'
    with Image.new('RGB', (10, 8), 'red') as image:
        image.save(target)
    control, called = RunControl(), []
    from naiyue_test_agent.tools import images
    if stage == 'decode':
        original = images.ImageOps.exif_transpose
        def transpose(*args, **kwargs):
            result = original(*args, **kwargs)
            control.request()
            called.append(stage)
            return result
        monkeypatch.setattr(images.ImageOps, 'exif_transpose', transpose)
    else:
        method = 'thumbnail' if stage == 'resize' else 'save'
        original = getattr(Image.Image, method)
        def operation(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            control.request()
            called.append(stage)
            return result
        monkeypatch.setattr(Image.Image, method, operation)
    with pytest.raises(ToolException, match='stopped'):
        invoke_body(create_session_read_tool(), tmp_path, {'path': target.name}, control)
    assert called == [stage]


def test_write_stop_between_parent_creation_preserves_specific_feedback(monkeypatch, tmp_path):
    control = RunControl()
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == tmp_path / 'new':
            control.request()
        return result
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    with pytest.raises(ToolException, match='stopped') as caught:
        invoke_body(create_session_write_tool(), tmp_path,
                    {'path': 'new/leaf/file.txt', 'content': 'replacement'}, control)
    assert 'parent directories may' in str(caught.value)
    assert 'target changed' not in str(caught.value)
    assert (tmp_path / 'new').is_dir() and not (tmp_path / 'new/leaf').exists()
    assert not list(tmp_path.rglob('.naiyue-write-*'))


@pytest.mark.parametrize('name', ['write', 'edit'])
@pytest.mark.parametrize('stage', ['flush', 'fsync', 'replace'])
def test_commit_boundary_preserves_original_or_confirmed_success(monkeypatch, tmp_path, name, stage):
    target = tmp_path / 'file.txt'
    target.write_text('original')
    control = RunControl()
    if stage == 'flush':
        original = write_module.tempfile.NamedTemporaryFile
        def temporary(*args, **kwargs):
            output = original(*args, **kwargs)
            flush = output.flush
            def flush_and_stop():
                flush()
                control.request()
            output.flush = flush_and_stop
            return output
        monkeypatch.setattr(write_module.tempfile, 'NamedTemporaryFile', temporary)
    elif stage == 'fsync':
        original = write_module.os.fsync
        def sync_and_stop(fd):
            original(fd)
            control.request()
        monkeypatch.setattr(write_module.os, 'fsync', sync_and_stop)
    else:
        module = write_module if name == 'write' else edit_module
        original = module._replace_temp
        def replace_and_stop(*args, **kwargs):
            result = original(*args, **kwargs)
            control.request()
            return result
        monkeypatch.setattr(module, '_replace_temp', replace_and_stop)
    spec = create_session_write_tool() if name == 'write' else create_session_edit_tool()
    args = {'path': target.name, **({'content': 'replacement'} if name == 'write' else
            {'edits': [{'oldText': 'original', 'newText': 'replacement'}]})}
    if stage == 'replace':
        result = invoke_body(spec, tmp_path, args, control)
        assert 'replaced' in result[0]
        assert target.read_bytes() == b'replacement'
    else:
        with pytest.raises(ToolException, match='stopped'):
            invoke_body(spec, tmp_path, args, control)
        assert target.read_bytes() == b'original'
    assert control.requested and not list(tmp_path.glob('.naiyue-write-*'))


@pytest.mark.parametrize('phase,old', [('scan', 'original'), ('fuzzy', '"original"'), ('copy', 'original')])
def test_edit_scan_fuzzy_and_copy_loops_stop_before_more_io(monkeypatch, tmp_path, phase, old):
    target = tmp_path / 'file.txt'
    before = ('“original”\n' + 'tail\n' * 50).encode()
    target.write_bytes(before)
    original = Path.open
    control, streams, opens, reads = RunControl(), [], 0, []
    monkeypatch.setattr(edit_module, '_CHUNK_BYTES', 16)
    wanted_open = {'scan': 1, 'fuzzy': 2, 'copy': 2}[phase]
    def open_file(path, mode='r', *args, **kwargs):
        nonlocal opens
        source = original(path, mode, *args, **kwargs)
        if path == target and mode == 'rb':
            opens += 1
            streams.append(source)
            if opens == wanted_open:
                def after(method, size):
                    assert not control.requested, 'Edit continued IO after the stop boundary'
                    reads.append(size)
                    if size == 16:
                        control.request()
                return ObservedStream(source, after)
        return source
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(ToolException, match='stopped'):
        invoke_body(create_session_edit_tool(), tmp_path,
                    {'path': target.name, 'edits': [{'oldText': old, 'newText': 'replacement'}]}, control)
    assert control.requested and reads and all(s.closed for s in streams)
    with original(target, 'rb') as source:
        assert source.read() == before
    assert not list(tmp_path.glob('.naiyue-write-*'))


@pytest.mark.parametrize('name', ['write', 'edit'])
def test_busy_held_until_stopped_temp_cleanup_settles(monkeypatch, tmp_path, name):
    target = tmp_path / 'file.txt'
    target.write_text('original')
    control, entered, release = RunControl(), Event(), Event()
    original_sync, original_unlink = write_module.os.fsync, Path.unlink
    def sync(fd):
        original_sync(fd)
        control.request()
    def unlink(path, *args, **kwargs):
        if path.name.startswith('.naiyue-write-'):
            entered.set()
            assert release.wait(5)
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(write_module.os, 'fsync', sync)
    monkeypatch.setattr(Path, 'unlink', unlink)
    spec = create_session_write_tool() if name == 'write' else create_session_edit_tool()
    args = {'path': target.name, **({'content': 'replacement'} if name == 'write' else
            {'edits': [{'oldText': 'original', 'newText': 'replacement'}]})}
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(invoke_body, spec, tmp_path, args, control)
        try:
            assert entered.wait(5)
            with pytest.raises(ToolException, match='busy'):
                invoke_body(create_session_write_tool(), tmp_path,
                            {'path': target.name, 'content': 'other'}, RunControl())
            assert target.read_bytes() == b'original'
        finally:
            release.set()
        with pytest.raises(ToolException, match='stopped'):
            task.result(timeout=5)
    monkeypatch.setattr(write_module.os, 'fsync', original_sync)
    result = invoke_body(create_session_write_tool(), tmp_path,
                         {'path': target.name, 'content': 'after'}, RunControl())
    assert 'replaced' in result[0] and target.read_bytes() == b'after'
    assert not list(tmp_path.glob('.naiyue-write-*'))


@pytest.mark.parametrize('name', ['write', 'edit'])
def test_real_io_failure_during_stop_keeps_cause_and_original_bytes(monkeypatch, tmp_path, name, caplog):
    target = tmp_path / 'file.txt'
    target.write_text('original')
    control, failure = RunControl(), OSError('PRIVATE_IO_DETAIL')
    def fail(fd):
        control.request()
        raise failure
    monkeypatch.setattr(write_module.os, 'fsync', fail)
    spec = create_session_write_tool() if name == 'write' else create_session_edit_tool()
    args = {'path': target.name, **({'content': 'replacement'} if name == 'write' else
            {'edits': [{'oldText': 'original', 'newText': 'replacement'}]})}
    with pytest.raises(ToolException, match='I/O') as caught:
        invoke_body(spec, tmp_path, args, control)
    assert caught.value.__cause__ is failure and control.requested
    assert target.read_bytes() == b'original' and not list(tmp_path.glob('.naiyue-write-*'))
    assert 'OSError' in caplog.text and 'PRIVATE_IO_DETAIL' not in caplog.text + str(caught.value)


@pytest.mark.parametrize('name', ['write', 'edit'])
def test_stopped_temp_cleanup_failure_is_visible_without_overwriting_primary_error(monkeypatch, tmp_path, name, caplog):
    target = tmp_path / 'file.txt'
    target.write_text('original')
    control, unlinked = RunControl(), []
    original_sync, original_unlink = write_module.os.fsync, Path.unlink
    def sync(fd):
        original_sync(fd)
        control.request()
    def unlink(path, *args, **kwargs):
        if path.name.startswith('.naiyue-write-'):
            unlinked.append(path)
            raise PermissionError('PRIVATE_CLEANUP_DETAIL')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(write_module.os, 'fsync', sync)
    monkeypatch.setattr(Path, 'unlink', unlink)
    spec = create_session_write_tool() if name == 'write' else create_session_edit_tool()
    args = {'path': target.name, **({'content': 'replacement'} if name == 'write' else
            {'edits': [{'oldText': 'original', 'newText': 'replacement'}]})}
    with pytest.raises(ToolException, match='stopped') as caught:
        invoke_body(spec, tmp_path, args, control)
    assert target.read_bytes() == b'original' and len(unlinked) == 1
    assert unlinked[0].exists()
    assert f'{name}_temp_cleanup_failed error_type=PermissionError' in caplog.text
    assert 'PRIVATE_CLEANUP_DETAIL' not in caplog.text + str(caught.value)
    original_unlink(unlinked[0])
    monkeypatch.setattr(write_module.os, 'fsync', original_sync)
    assert 'replaced' in invoke_body(create_session_write_tool(), tmp_path,
                                    {'path': target.name, 'content': 'after'}, RunControl())[0]


@pytest.mark.parametrize('name', ['write', 'edit'])
def test_stop_during_final_target_recheck_prevents_commit(monkeypatch, tmp_path, name):
    target = tmp_path / 'file.txt'
    target.write_text('original')
    module = write_module if name == 'write' else edit_module
    recheck = '_resolve_write_target' if name == 'write' else 'verify_file_target'
    final_check = 3 if name == 'write' else 2
    original, calls, control = getattr(module, recheck), [], RunControl()
    def check(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == final_check:
            control.request()
        return result
    monkeypatch.setattr(module, recheck, check)
    spec = create_session_write_tool() if name == 'write' else create_session_edit_tool()
    args = {'path': target.name, **({'content': 'replacement'} if name == 'write' else
            {'edits': [{'oldText': 'original', 'newText': 'replacement'}]})}
    with pytest.raises(ToolException, match='stopped'):
        invoke_body(spec, tmp_path, args, control)
    assert len(calls) == final_check and control.requested
    assert target.read_bytes() == b'original'
    assert not list(tmp_path.glob('.naiyue-write-*'))


@pytest.mark.parametrize('name', ['write', 'edit'])
def test_stop_inside_started_replace_waits_for_true_result_and_holds_busy(monkeypatch, tmp_path, name):
    target = tmp_path / 'file.txt'
    target.write_text('original')
    module = write_module if name == 'write' else edit_module
    original, control, entered, release = module._replace_temp, RunControl(), Event(), Event()
    def replace(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_replace_temp', replace)
    spec = create_session_write_tool() if name == 'write' else create_session_edit_tool()
    args = {'path': target.name, **({'content': 'replacement'} if name == 'write' else
            {'edits': [{'oldText': 'original', 'newText': 'replacement'}]})}
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(invoke_body, spec, tmp_path, args, control)
        try:
            assert entered.wait(5)
            control.request()
            assert not task.done() and target.read_bytes() == b'original'
            with pytest.raises(ToolException, match='busy'):
                invoke_body(create_session_write_tool(), tmp_path,
                            {'path': target.name, 'content': 'other'}, RunControl())
        finally:
            release.set()
        result = task.result(timeout=5)
    assert 'replaced' in result[0]
    assert target.read_bytes() == b'replacement' and not list(tmp_path.glob('.naiyue-write-*'))
