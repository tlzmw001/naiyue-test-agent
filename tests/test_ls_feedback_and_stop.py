"""A directory result explicitly accounts for failed entries and cooperative stop."""

from native_application_helpers import async_test

import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.tools import LsSettings, create_session_ls_tool, create_session_write_tool
from naiyue_test_agent.tools import ls as ls_module
from test_ls_tool import call_ls
from test_async_application import StreamingToolModel, make_app


class Entry:
    def __init__(self, root, name, error=None, inspect=None):
        self.name, self.path, self.error, self.inspect = name, str(root / name), error, inspect
    def is_dir(self, **kwargs):
        return False
    def stat(self, **kwargs):
        if self.inspect:
            self.inspect()
        if self.error:
            raise self.error
        return SimpleNamespace()


class Scan:
    def __init__(self, entries):
        self.entries, self.closed = entries, False
    def __enter__(self):
        return iter(self.entries)
    def __exit__(self, *args):
        self.closed = True


@async_test
@pytest.mark.parametrize('error,reason', [(FileNotFoundError('PRIVATE'), 'disappeared'),
    (PermissionError('PRIVATE'), 'permission_denied'), (OSError('PRIVATE'), 'io_error')])
@pytest.mark.parametrize('ordinary', [True, False])
async def test_stat_fault_is_reported_and_never_looks_like_empty(monkeypatch, tmp_path, caplog, error, reason, ordinary):
    entries = ([Entry(tmp_path, 'a.txt')] if ordinary else []) + [Entry(tmp_path, 'b\n.txt', error)]
    scan = Scan(entries)
    monkeypatch.setattr(ls_module.os, 'scandir', lambda _: scan)
    result = (await call_ls(create_session_ls_tool(), {}, tmp_path))
    assert result.status == 'success' and '(empty directory)' not in result.content
    assert 'incomplete' in result.content and 'b\\n.txt: ' + reason in result.content
    assert result.content.startswith('a.txt\n') if ordinary else result.content.startswith('[ls:')
    assert scan.closed and 'PRIVATE' not in result.content + caplog.text
    assert type(error).__name__ in caplog.text


@async_test
@pytest.mark.parametrize('error,expected', [(FileNotFoundError('PRIVATE'), ('missing', 'Check')),
    (NotADirectoryError('PRIVATE'), ('directory', 'Choose')),
    (PermissionError('PRIVATE'), ('permission', 'Check'))])
async def test_open_failure_has_specific_safe_message(monkeypatch, tmp_path, caplog, error, expected):
    def fail(_):
        raise error
    monkeypatch.setattr(ls_module.os, 'scandir', fail)
    result = (await call_ls(create_session_ls_tool(), {}, tmp_path))
    assert result.status == 'error' and all(word in result.content for word in expected)
    assert type(error).__name__ in caplog.text and 'PRIVATE' not in result.content + caplog.text


def test_already_stopped_listing_never_opens_directory(monkeypatch, tmp_path):
    visits = []
    monkeypatch.setattr(ls_module.os, 'scandir', lambda _: visits.append(True))
    runtime = SimpleNamespace(context={'_run_control': SimpleNamespace(requested=True)})
    with pytest.raises(ToolException, match='stopped'):
        ls_module._list_directory(tmp_path, 2, runtime, settings=LsSettings())
    assert visits == []


def test_stop_during_enumeration_closes_iterator_and_does_not_expose_unfiltered_names(monkeypatch, tmp_path):
    control = SimpleNamespace(requested=False)
    def entries():
        yield Entry(tmp_path, 'PRIVATE_UNREVIEWED')
        control.requested = True
        yield Entry(tmp_path, 'PRIVATE_LATE')
        pytest.fail('A stopped listing must not keep enumerating')
    scan = Scan(entries())
    monkeypatch.setattr(ls_module.os, 'scandir', lambda _: scan)
    runtime = SimpleNamespace(context={'_run_control': control})
    with pytest.raises(ToolException, match='stopped') as caught:
        ls_module._list_directory(tmp_path, 2, runtime, settings=LsSettings())
    assert scan.closed and 'PRIVATE' not in str(caught.value)


def test_listing_stop_retains_allowed_prefix_and_run_finishes_stopped(monkeypatch, tmp_path):
    async def scenario():
        entered, release = Event(), Event()
        def hold():
            entered.set()
            assert release.wait(5)
        scan = Scan([Entry(tmp_path, 'a.txt'), Entry(tmp_path, 'b.txt', inspect=hold)])
        monkeypatch.setattr(ls_module.os, 'scandir', lambda _: scan)
        response = AIMessage(content='', tool_calls=[{'name': 'ls', 'args': {}, 'id': 'ls'},
            {'name': 'write', 'args': {'path': 'new/file.txt', 'content': 'not written'}, 'id': 'write'}])
        app = await make_app(monkeypatch, StreamingToolModel(replies=[response]),
            [create_session_ls_tool(), create_session_write_tool()])
        sid = await app.acreate_workspace_session('A', tmp_path)
        events = []
        app.subscribe_events(events.append)
        task = asyncio.create_task(app.arun(sid, [('user', 'list')]))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            app.request_stop(sid)
            release.set()
            result = await asyncio.wait_for(task, 5)
            outputs = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
            feedback = outputs['ls']
            assert feedback.status == 'success' and feedback.content.startswith('a.txt\n')
            assert 'incomplete' in feedback.content and 'stopped' in feedback.content and 'b.txt' not in feedback.content
            assert result['run_stopped'] and events[-1].data.status == 'stopped'
            assert app._sessions.describe_session(sid)['busy'] is False and scan.closed
            assert len([e for e in events if e.event_type == 'model_call_started']) == 1
            assert outputs['write'].status == 'error' and outputs['write'].additional_kwargs['naiyue_not_executed'] == 'user_stop'
            assert not (tmp_path / 'new').exists()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await app.aclose()
    asyncio.run(scenario())


@async_test
async def test_many_failed_entries_keep_the_skip_report_bounded(monkeypatch, tmp_path):
    scan = Scan([Entry(tmp_path, f'{number:03d}-entry.txt', OSError('PRIVATE')) for number in range(100)])
    monkeypatch.setattr(ls_module.os, 'scandir', lambda _: scan)
    result = (await call_ls(create_session_ls_tool(settings=LsSettings(max_output_bytes=128)), {}, tmp_path))
    assert result.status == 'success' and 'incomplete' in result.content and 'omitted' in result.content
    assert len(result.content.encode()) <= 228 and 'PRIVATE' not in result.content
