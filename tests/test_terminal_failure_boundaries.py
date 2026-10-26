"""Unknown command outcomes, malformed observations and failed close stay distinct."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from naiyue_test_agent.cli import chat
from naiyue_test_agent.cli.cli_approval import handle_approval, handle_scope_stop, settle_cli_worker
from naiyue_test_agent.cli.terminal_client import TerminalClient
from naiyue_test_agent.service.settings import ServiceError
from naiyue_test_agent.settings import AppSettings


@pytest.mark.parametrize('kind', ['paused_root', 'live_child'])
@pytest.mark.parametrize('code', ['service_connection_failed', 'stale_approval'])
def test_approval_failure_distinguishes_unknown_from_explicit_rejection(kind, code, caplog):
    batch = dict(kind=kind, approval_batch_id='batch', origin_run_id='run', items=[{'approval_id': 'one'}])
    calls, output, resumed = [], [], []
    class Client:
        def pending_approvals(self, sid): return [batch]
        def approve(self, *args):
            calls.append(args)
            raise ServiceError(code)
    handle_approval(Client(), 's', ':approve', worker=None, interactive=True,
        write=output.append, start_root=resumed.append)
    assert len(calls) == 1 and resumed == []
    assert '需要为' not in '\n'.join(output)
    assert ('结果未确认' if code == 'service_connection_failed' else '未被接受') in '\n'.join(output)
    assert code in caplog.text


def test_invalid_approval_arguments_do_not_submit():
    output = []
    class Client:
        def pending_approvals(self, sid):
            return [dict(kind='paused_root', approval_batch_id='b', origin_run_id='r', items=[{'approval_id': 'one'}])]
        def approve(self, *args): pytest.fail('Invalid input must not be submitted')
    handle_approval(Client(), 's', ':review approve reject', worker=None, interactive=True,
        write=output.append, start_root=lambda _: pytest.fail('No resume'))
    assert '需要为 1 个调用分别给出决定' in '\n'.join(output)


class InputClient:
    disconnecting = False
    def request(self, *args, **kwargs): return {'default_model_id': 'A'}
    def watch(self, *args): pass
    def pending_approvals(self, sid): return []
    def active_run(self, sid): return 'run'
    def close(self): pass


@pytest.mark.parametrize('target', ['root', 'child'])
@pytest.mark.parametrize('code', ['service_connection_failed', 'stale_run'])
def test_stop_failure_distinguishes_unknown_from_rejection(monkeypatch, capsys, caplog, target, code):
    client, calls = InputClient(), []
    def stop(*args, **kwargs):
        calls.append((args, kwargs))
        raise ServiceError(code)
    client.control = stop
    monkeypatch.setattr(chat, 'load_settings', AppSettings)
    monkeypatch.setattr(chat, 'prepare_terminal', lambda *args: (client, 's', None))
    monkeypatch.setattr(chat.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    lines = iter([':stop ' + target, ':quit'])
    monkeypatch.setattr('builtins.input', lambda _: next(lines))
    assert chat.main(['--no-session']) == 0
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert ('结果未确认' if code == 'service_connection_failed' else '未被接受') in output
    assert code in caplog.text


def test_eof_does_not_retry_unconfirmed_stop(caplog):
    calls = []
    class Client:
        def pending_approvals(self, sid): return [{'kind': 'live_child'}]
        def control(self, *args):
            calls.append(args)
            raise ServiceError('service_connection_failed')
    # A second loop would appear idle, making the missing exception fail promptly.
    states = iter([True, False])
    worker = SimpleNamespace(is_alive=lambda: next(states), join=lambda *_: None)
    with pytest.raises(ServiceError, match='service_connection_failed'):
        settle_cli_worker(Client(), 's', worker, lambda _: None)
    assert len(calls) == 1 and 'service_connection_failed' in caplog.text


@pytest.mark.parametrize('failure', ['invalid_json', 'invalid_event'])
def test_bad_event_closes_stream_and_polls_until_done_before_history(failure, caplog):
    async def scenario():
        calls, closed, history_at = [], [], []
        class HTTP:
            async def events(self, *args, **kwargs):
                try:
                    if failure == 'invalid_json': raise ServiceError('service_response_invalid')
                    yield {'event': 'runtime', 'data': {}}
                finally:
                    closed.append(True)
            async def request(self, method, path, **kwargs):
                calls.append((method, path))
                assert closed == [True], 'The broken SSE must close before polling'
                return {'done': len(calls) >= 2, 'status': 'completed' if len(calls) >= 2 else 'running'}
        client = object.__new__(TerminalClient)
        displayed = []
        class Display:
            def begin_call(self): pass
            def __call__(self, event): pytest.fail('Invalid event must not be displayed')
            def finish_view(self, result): displayed.append(result)
        client._http, client._display, client.scope_id = HTTP(), Display(), 'chats'
        client._cursors, client._observed_messages = {}, {}
        client.options = SimpleNamespace(session_poll_interval=0, max_read_reconnects=0)
        async def history(_):
            history_at.append(len(calls))
            return {'messages': [{'id': 'answer', 'type': 'ai', 'content': 'confirmed'}]}
        client._history = history
        result = await client._consume('s', 'r')
        assert result['run']['status'] == 'completed' and result['reply_messages'][0]['content'] == 'confirmed'
        assert history_at == [2] and all(method == 'GET' for method, _ in calls)
        assert displayed == [result]
        assert client._cursors == {} and 'terminal_event_fallback' in caplog.text
    asyncio.run(scenario())


@pytest.mark.parametrize('primary', [None, KeyboardInterrupt('interrupted'), RuntimeError('input failure')])
def test_cli_close_failure_joins_worker_and_preserves_primary(monkeypatch, capsys, caplog, primary):
    client, joined, inputs = InputClient(), [], []
    client.active_run = lambda _: None
    def close(): raise ServiceError('service_connection_failed')
    client.close = close
    class Worker:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def join(self): joined.append(True)
    monkeypatch.setattr(chat, 'Thread', Worker)
    monkeypatch.setattr(chat, 'load_settings', AppSettings)
    monkeypatch.setattr(chat, 'prepare_terminal', lambda *args: (client, 's', None))
    monkeypatch.setattr(chat.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    def read(_):
        inputs.append(True)
        if len(inputs) == 1: return 'question'
        if primary is not None: raise primary
        return ':quit'
    monkeypatch.setattr('builtins.input', read)
    if primary is None:
        assert chat.main(['--no-session']) == 1
    else:
        with pytest.raises(type(primary)) as caught:
            chat.main(['--no-session'])
        assert caught.value is primary
    assert joined == [True]
    assert '清理结果未确认' in capsys.readouterr().out and 'cli_close_failed' in caplog.text


def test_network_close_keeps_first_failure_and_clears_local_results(caplog):
    async def scenario():
        client = object.__new__(TerminalClient)
        primary = ServiceError('service_connection_failed')
        client._http = SimpleNamespace(request=AsyncMock(side_effect=primary),
            aclose=AsyncMock(side_effect=OSError('PRIVATE_CLOSE_ERROR')))
        client._closed, client._temporary, client.scope_id = False, True, 'temp-a'
        client._streams, client._cursors, client._observed_messages = {'r': asyncio.create_task(asyncio.sleep(0))}, {'r': 1}, {'secret': {}}
        client._watcher = client._lease = client._display = None
        stopped, joined = [], []
        client._loop = SimpleNamespace(stop=lambda: None, call_soon_threadsafe=lambda fn: stopped.append(True))
        client._thread = SimpleNamespace(join=lambda: joined.append(True))
        # Run close()'s coroutine on this existing owner loop without a network thread.
        loop = asyncio.get_running_loop()
        def dispatch(operation):
            future = asyncio.run_coroutine_threadsafe(operation(), loop)
            return future.result(5)
        client._submit = dispatch
        with pytest.raises(ServiceError) as caught:
            await asyncio.to_thread(client.close)
        assert caught.value is primary
        client._http.aclose.assert_awaited_once()
        assert client._closed and stopped == joined == [True]
        assert client._streams == client._cursors == client._observed_messages == {}
        assert 'terminal_close_secondary_failed' in caplog.text and 'PRIVATE_CLOSE_ERROR' not in caplog.text
    asyncio.run(scenario())


def test_paused_root_stop_failure_is_presented_without_a_new_run(caplog):
    output, submitted = [], []
    client = SimpleNamespace(pending_approvals=lambda sid: [{'kind': 'paused_root'}])
    def start(batch):
        submitted.append(batch)
        raise ServiceError('service_connection_failed')
    assert handle_scope_stop(client, 's', 'root', worker=None, write=output.append, start_root=start)
    assert len(submitted) == 1 and '结果未确认' in '\n'.join(output)
    assert 'service_connection_failed' in caplog.text


def test_failed_snapshot_is_not_swallowed_by_bad_event_fallback():
    async def scenario():
        primary = ServiceError('operation_failed')
        class HTTP:
            async def events(self, *args, **kwargs): yield {'data': {}}
            async def request(self, *args, **kwargs): raise primary
        client = object.__new__(TerminalClient)
        client._http, client._display, client.scope_id = HTTP(), None, 'chats'
        client._cursors, client._observed_messages = {}, {}
        client.options = SimpleNamespace(max_read_reconnects=0)
        with pytest.raises(ServiceError) as caught:
            await client._consume('s', 'r')
        assert caught.value is primary
    asyncio.run(scenario())


def test_real_temporary_close_response_lost_still_cleans_host_and_network(monkeypatch, tmp_path, capsys):
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import StreamingToolModel
    from test_service_ownership import options
    from terminal_service_helpers import TerminalService
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': StreamingToolModel(replies=[])}))
    with TerminalService(options(tmp_path)) as server:
        client = server.client()
        client.lease(tmp_path)
        identity = client.scope_id
        sid = client.request('POST', '/sessions', json={'model_id': 'A'})['session_id']
        original = client._http.request
        async def lost(method, path, **kwargs):
            value = await original(method, path, **kwargs)
            if method == 'DELETE': raise ServiceError('service_connection_failed')
            return value
        monkeypatch.setattr(client._http, 'request', lost)
        monkeypatch.setattr(chat, 'load_settings', AppSettings)
        monkeypatch.setattr(chat, 'prepare_terminal', lambda *args: (client, sid, None))
        monkeypatch.setattr(chat.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
        monkeypatch.setattr('builtins.input', lambda _: ':quit')
        try:
            assert chat.main(['--no-session']) == 1
            assert client._closed and not client._thread.is_alive()
            assert server.app.state.temporary.tickets[identity].closed.is_set()
            assert identity not in server.app.state.scopes.services
            assert '清理结果未确认' in capsys.readouterr().out
        finally:
            client.close()
