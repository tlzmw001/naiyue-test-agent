"""Transient reads and late clients cannot replay controls or lose a running chat."""
import asyncio
from types import SimpleNamespace
from threading import Event
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from naiyue_test_agent.cli import chat
from naiyue_test_agent.cli.terminal_client import TerminalClient
from naiyue_test_agent.service.settings import ServiceError
from naiyue_test_agent.settings import AppSettings


@pytest.mark.parametrize('line', ['payload', ':follow payload'])
def test_unconfirmed_control_is_retained_and_never_reissued_as_run(monkeypatch, capsys, line):
    runs = []
    class Client:
        disconnecting = False
        def request(self, *args, **kwargs): return {'default_model_id': 'A'}
        def watch(self, *args): pass
        def pending_approvals(self, sid): return []
        def active_run(self, sid): return 'r'
        def control(self, *args, **kwargs): raise ServiceError('service_connection_failed')
        def run(self, sid, text):
            runs.append(text)
            return {'reply_messages': [], 'run': {'status': 'completed'}}
        def close(self): pass
    monkeypatch.setattr(chat, 'load_settings', AppSettings)
    monkeypatch.setattr(chat, 'prepare_terminal', lambda *args: (Client(), 's', None))
    monkeypatch.setattr(chat.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    entries = iter([line, ':pending', ':quit'])
    monkeypatch.setattr('builtins.input', lambda _: next(entries))
    assert chat.main(['--no-session']) == 0
    assert runs == []
    output = capsys.readouterr().out
    assert '结果未确认' in output and 'payload' in output and '待发送内容' in output


def test_late_terminal_resumes_current_service_run_and_can_stop(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from naiyue_test_agent.cli import chat_startup
    from test_async_application import StreamingToolModel
    from test_service_ownership import options
    from terminal_service_helpers import TerminalService
    entered = Event()
    model = StreamingToolModel(replies=[AIMessage(content='must not finish')], entered=entered, release=asyncio.Event())
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    root = tmp_path / 'project'
    root.mkdir()
    with TerminalService(options(tmp_path)) as server:
        a = server.client()
        try:
            selected = a.request('POST', '/api/v1/terminal/prepare', json={'root': str(root), 'trusted': True})
            a.bind(selected['scope_id'])
            sid = a.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
            rid = a.request('POST', f'/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'wait'})['run_id']
            assert entered.wait(5)
            monkeypatch.setattr(chat_startup, 'TerminalClient', lambda _: server.client())
            monkeypatch.setattr(chat.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
            entries = iter([':stop', ':quit'])
            consumed = []
            def read(_):
                value = next(entries)
                consumed.append(value)
                return value
            monkeypatch.setattr('builtins.input', read)
            assert chat.main(['--project', selected['scope_id'], '--resume', sid]) == 0
            assert consumed == [':stop', ':quit']
            assert a.wait(sid, rid)['run']['status'] == 'stopped'
            assert len(model.seen) == 1
            assert '当前没有运行' not in capsys.readouterr().out
        finally:
            a.close()


def test_resync_waits_until_terminal_snapshot_without_mutating_requests():
    async def scenario():
        calls = []
        class HTTP:
            async def events(self, *args, **kwargs):
                yield {'event': 'resync_required', 'data': {}}
            async def request(self, method, path, **kwargs):
                calls.append((method, path))
                return {'run_id': 'r', 'done': len(calls) >= 2, 'status': 'completed' if len(calls) >= 2 else 'running'}
        client = object.__new__(TerminalClient)
        client._http, client._display, client.scope_id = HTTP(), None, 'chats'
        client._cursors, client._observed_messages = {}, {}
        client.options = SimpleNamespace(session_poll_interval=0)
        async def history(_): return {'messages': []}
        client._history = history
        await client._consume('s', 'r')
        assert len(calls) >= 2 and all(method == 'GET' for method, _ in calls)
    asyncio.run(scenario())


def test_normal_event_eof_keeps_cursor_until_events_are_consumed():
    async def scenario():
        cursors, views = [], []
        class HTTP:
            async def events(self, scope, run, *, after):
                cursors.append(after)
                if False: yield None
            async def request(self, method, path):
                views.append(path)
                return {'done': len(views) == 2, 'last_sequence': 7, 'status': 'running'}
        client = object.__new__(TerminalClient)
        client._http, client._display, client.scope_id = HTTP(), None, 'chats'
        client._cursors, client._observed_messages = {}, {}
        client.options = SimpleNamespace(session_poll_interval=0)
        async def history(_): return {'messages': []}
        client._history = history
        await client._consume('s', 'r')
        assert cursors == [0, 0], 'EOF cannot skip buffered facts using the newer status cursor'
    asyncio.run(scenario())


@pytest.mark.parametrize('replacement', [False, True])
def test_read_retry_pins_service_instance_and_never_replays_a_command(monkeypatch, tmp_path, replacement):
    import naiyue_test_agent.cli.terminal_client as module
    from test_service_ownership import options
    config = options(tmp_path)
    calls = []
    class HTTP:
        instance_id = 'a' * 32
        async def request(self, method, path, **kwargs):
            calls.append((method, path))
            if len(calls) == 1: raise ServiceError('service_connection_failed')
            if path.endswith('handshake'):
                return {'identity': config.identity.model_dump(mode='json'), 'instance_id': self.instance_id}
            return {'ok': True}
    monkeypatch.setattr(module, 'read_discovery', lambda _: SimpleNamespace(instance_id=('b' if replacement else 'a') * 32))
    client = object.__new__(TerminalClient)
    client.options, client._http = config, HTTP()
    async def scenario():
        if replacement:
            with pytest.raises(ServiceError, match='service_instance_mismatch'):
                await client._read('/api/v1/projects')
        else:
            assert await client._read('/api/v1/projects') == {'ok': True}
    asyncio.run(scenario())
    assert calls == ([('GET', '/api/v1/projects')] if replacement else [
        ('GET', '/api/v1/projects'), ('POST', '/api/v1/handshake'), ('GET', '/api/v1/projects')])


def test_empty_terminal_observer_discovers_first_already_completed_run():
    async def scenario():
        discovered = asyncio.Event()
        client = object.__new__(TerminalClient)
        client.scope_id = 'chats'
        client.options = SimpleNamespace(session_poll_interval=0)
        async def read(_): return {'live': {'run_id': 'first', 'done': True}}
        def stream(sid, rid):
            assert (sid, rid) == ('s', 'first')
            discovered.set()
        client._read, client._stream = read, stream
        task = asyncio.create_task(client._watch_session('s', None))
        try:
            await asyncio.wait_for(discovered.wait(), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_persistent_terminal_disconnect_leaves_run_for_second_terminal(monkeypatch, tmp_path):
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import StreamingToolModel
    from test_service_ownership import options
    from terminal_service_helpers import TerminalService
    entered, release = Event(), asyncio.Event()
    model = StreamingToolModel(replies=[AIMessage(content='survived')], entered=entered, release=release)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    with TerminalService(options(tmp_path)) as server:
        a, b = server.client(), server.client()
        try:
            a.bind('chats')
            b.bind('chats')
            sid = a.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
            rid = a.request('POST', f'/sessions/{sid}/runs', json={'request_id': str(uuid4()), 'text': 'question'})['run_id']
            assert entered.wait(5)
            a.close()
            assert b.request('GET', f'/runs/{rid}')['status'] == 'running'
            assert not release.is_set()
            server.loop.call_soon_threadsafe(release.set)
            assert b.wait(sid, rid)['messages'][-1]['content'] == 'survived'
            assert len(model.seen) == 1
        finally:
            a.close()
            b.close()
