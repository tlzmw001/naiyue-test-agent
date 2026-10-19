"""Search authorization, native async ownership, and incomplete-result contracts."""

from native_application_helpers import async_test

import json
import threading

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools import create_session_find_tool, create_session_grep_tool, FindSettings
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.processes import HostProcessRunner, ProcessOutcome, ProcessCleanupError
from naiyue_test_agent.tools.process_protocol import ProcessProtocol
from test_async_application import StreamingToolModel, make_app, call
from test_search_streaming import invoke


@async_test
async def test_external_batch_starts_nothing_before_approval_and_filters_before_content(monkeypatch, tmp_path):
    async def scenario():
        root, outside = tmp_path / 'workspace', tmp_path / 'external'
        root.mkdir(); outside.mkdir()
        for name in ('ordinary.txt', 'denied.txt', '.hidden', 'key.pem'):
            (outside / name).write_text('NEEDLE_' + name)
        (outside / 'loop').symlink_to(outside, target_is_directory=True)
        calls, threads = [], []
        original = HostProcessRunner.run
        async def record(self, request, *args, **kwargs):
            calls.append(request.argv)
            threads.append(threading.get_ident())
            assert request.eof_policy == 'complete' and not request.merge_output
            assert request.execution_environment['kind'] == 'host'
            return await original(self, request, *args, **kwargs)
        monkeypatch.setattr(HostProcessRunner, 'run', record)
        model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
            {'name': 'grep', 'id': 'g', 'args': {'pattern': 'NEEDLE', 'path': str(outside)}},
            {'name': 'find', 'id': 'f', 'args': {'pattern': '*', 'path': str(outside)}},
        ]), AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        settings = AppSettings.model_validate({'permissions': {'deny': [
            {'tool': '*', 'path': str(outside / 'denied.txt')}]}})
        app = await build_async_local_application('unused', tools=[create_session_grep_tool(),
            create_session_find_tool()], settings=settings, system_prompt='', response_format=None)
        sid = await app.acreate_workspace_session('A', root)
        events = []
        app.subscribe_events(events.append)
        try:
            paused = await app.arun(sid, [('user', 'search outside')])
            assert len(paused['approval_requests']) == 2 and calls == []
            result = await app.aresume_tool_approval(sid, ['approve', 'approve'])
            messages = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
            assert set(messages) == {'g', 'f'} and all(m.status == 'success' for m in messages.values())
            assert 'NEEDLE_ordinary.txt' in messages['g'].content
            assert '.hidden: unapproved_private_or_external' in messages['g'].content
            assert 'key.pem: unapproved_private_or_external' in messages['g'].content
            assert 'denied.txt: deny' in messages['g'].content
            assert all('NEEDLE_' + n not in messages['g'].content for n in ('denied.txt', '.hidden', 'key.pem'))
            groups = [argv[argv.index('--') + 1:] for argv in calls if '--json' in argv]
            assert groups == [[str(outside / 'ordinary.txt')]]
            assert threads and set(threads) == {threading.get_ident()}
            assert result['messages'][-1].content == 'done' and len(model.seen) == 2
            assert events[-1].data.status == 'completed'
        finally:
            await app.aclose()
    await scenario()


@async_test
@pytest.mark.parametrize('name', ['grep', 'find'])
@pytest.mark.parametrize('have_result', [False, True])
async def test_timeout_keeps_partial_results_but_never_reports_empty_complete(monkeypatch, tmp_path, name, have_result):
    target = tmp_path / 'note.txt'
    target.write_text('HIT\n')
    async def stream(self, request, consume, control=None):
        if have_result and '--files' in request.argv:
            await consume('stdout', str(target).encode() + b'\0')
            return ProcessOutcome(0, 'exited', True, True, True, True, 0)
        if have_result:
            if '--json' in request.argv:
                data = {'type': 'match', 'data': {'path': {'text': str(target)},
                    'line_number': 1, 'lines': {'text': 'HIT\n'}}}
                await consume('stdout', json.dumps(data).encode() + b'\n')
            else:
                await consume('stdout', str(target).encode() + b'\0')
        return ProcessOutcome(-9, 'timeout', False, True, True, True, 0)
    monkeypatch.setattr(HostProcessRunner, 'run', stream)
    spec = create_session_grep_tool() if name == 'grep' else create_session_find_tool()
    args = {'pattern': 'HIT' if name == 'grep' else '*.txt'}
    result = (await invoke(spec, tmp_path, **args))
    assert result.status == ('success' if have_result else 'error')
    assert 'timeout' in result.content and ('incomplete' in result.content if have_result else 'No complete result' in result.content)
    assert 'No matches found' not in result.content and 'No files found' not in result.content
    if have_result:
        assert 'note.txt' in result.content


@async_test
@pytest.mark.parametrize('name', ['grep', 'find'])
async def test_result_limit_reaps_native_process_without_stopping_run(monkeypatch, tmp_path, name):
    protocols = []
    made = ProcessProtocol.connection_made
    def record(self, transport):
        protocols.append(self)
        made(self, transport)
    monkeypatch.setattr(ProcessProtocol, 'connection_made', record)
    for index in range(100):
        (tmp_path / f'{index:03d}.txt').write_text('HIT\n')
    spec = create_session_grep_tool() if name == 'grep' else create_session_find_tool()
    result = (await invoke(spec, tmp_path, pattern='HIT' if name == 'grep' else '*.txt', limit=1))
    assert result.status == 'success' and 'truncated' in result.content
    assert protocols and all(p.transport.get_returncode() is not None and p.closed for p in protocols)


@async_test
async def test_rg_config_cannot_override_reviewed_native_arguments(monkeypatch, tmp_path):
    config = tmp_path / 'rgconfig'
    config.write_text('--files-without-match\n')
    monkeypatch.setenv('RIPGREP_CONFIG_PATH', str(config))
    (tmp_path / 'target.txt').write_text('HIT\n')
    result = (await invoke(create_session_grep_tool(), tmp_path, pattern='HIT', glob='*.txt'))
    assert result.content == 'target.txt:1: HIT'


@async_test
async def test_fd_permission_filter_happens_before_result_count(monkeypatch, tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    target = root / 'visible.txt'
    outside = tmp_path / 'outside.txt'
    outside.touch(); target.touch()
    alias = root / 'external.txt'
    alias.symlink_to(outside)
    async def stream(self, request, consume, control=None):
        await consume('stdout', str(alias).encode() + b'\0' + str(target).encode() + b'\0')
        return ProcessOutcome(0, 'exited', True, True, True, True, 0)
    monkeypatch.setattr(HostProcessRunner, 'run', stream)
    result = (await invoke(create_session_find_tool(settings=FindSettings(default_limit=1)), root, pattern='*.txt'))
    assert result.status == 'success' and result.content.startswith('visible.txt\n')
    assert 'external.txt: unapproved_external_target' in result.content
    assert 'truncated' not in result.content


@async_test
async def test_approved_search_file_cannot_become_a_directory_before_execution(monkeypatch, tmp_path):
    from test_dynamic_tools import DynamicModel
    from test_permission_pipeline import build
    root, target = tmp_path / 'workspace', tmp_path / 'external'
    root.mkdir()
    target.write_text('original file')
    model = DynamicModel(responses=[call('grep', {'pattern': 'SECRET', 'path': str(target)}),
                                   AIMessage(content='target changed')])
    app = await build(monkeypatch, model, [create_session_grep_tool()])
    sid = await app.acreate_workspace_session('A', root)
    paused = await app.arun(sid, [('user', 'search this file')])
    assert paused['approval_requests']
    target.unlink()
    target.mkdir()
    (target / 'child.txt').write_text('SECRET_UNAPPROVED_CHILD')
    calls = []
    run = HostProcessRunner.run
    async def record(self, request, *args, **kwargs):
        calls.append(request.argv)
        return await run(self, request, *args, **kwargs)
    monkeypatch.setattr(HostProcessRunner, 'run', record)
    result = await app.aresume_tool_approval(sid, ['approve'])
    message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert message.tool_call_id == 't' and message.status == 'error'
    assert 'changed' in message.content.lower()
    assert calls == [] and 'SECRET_UNAPPROVED_CHILD' not in str(result)


@async_test
async def test_earlier_serial_write_can_create_directory_for_later_ls_and_search(monkeypatch, tmp_path):
    from test_dynamic_tools import DynamicModel
    from test_permission_pipeline import build
    from naiyue_test_agent.tools import create_session_write_tool, create_session_ls_tool
    model = DynamicModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'write', 'id': 'w', 'args': {'path': 'new/note.txt', 'content': 'HIT\n'}},
        {'name': 'ls', 'id': 'l', 'args': {'path': 'new'}},
        {'name': 'grep', 'id': 'g', 'args': {'path': 'new', 'pattern': 'HIT'}},
    ]), AIMessage(content='done')])
    app = await build(monkeypatch, model, [create_session_write_tool(), create_session_ls_tool(),
                                    create_session_grep_tool()])
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'create and inspect')])
    messages = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
    assert all(m.status == 'success' for m in messages.values())
    assert messages['l'].content == 'note.txt'
    assert messages['g'].content == 'note.txt:1: HIT'


@pytest.mark.parametrize('name', ['grep', 'find'])
@async_test
async def test_unconfirmed_cleanup_is_never_converted_to_search_feedback(monkeypatch, tmp_path, name):
    async def scenario():
        failure = ProcessCleanupError(facts={'process_reaped': False})
        async def fail(*args, **kwargs):
            raise failure
        monkeypatch.setattr(HostProcessRunner, 'run', fail)
        spec = create_session_grep_tool() if name == 'grep' else create_session_find_tool()
        model = StreamingToolModel(replies=[call(name, {'pattern': 'HIT' if name == 'grep' else '*'}),
                                             AIMessage(content='must not run')])
        app = await make_app(monkeypatch, model, [spec])
        sid = await app.acreate_workspace_session('A', tmp_path)
        try:
            with pytest.raises(ProcessCleanupError) as caught:
                await app.arun(sid, [('user', 'search')])
            assert caught.value is failure
            assert app._sessions.require(sid).describe_session()['manual_review_required']
            assert len(model.seen) == 1
        finally:
            await app.aclose()
    await scenario()
