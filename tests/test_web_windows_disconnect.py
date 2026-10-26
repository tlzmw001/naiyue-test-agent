"""Exercise CPython's real Proactor close callback with deterministic socket faults."""

import asyncio
from asyncio.proactor_events import _ProactorSocketTransport
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


class ResetSocket:
    def __init__(self, error, close_error=None):
        self.error, self.close_error, self.closed = error, close_error, False

    def fileno(self): return 1

    def shutdown(self, _how): raise self.error

    def close(self):
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


def reset(code=10054):
    error = ConnectionResetError('PRIVATE')
    error.winerror = code
    return error


def transport(error, close_error=None):
    item = object.__new__(_ProactorSocketTransport)
    item._called_connection_lost = False
    item._closing = True
    item._protocol = SimpleNamespace(connection_lost=Mock())
    item._sock = ResetSocket(error, close_error)
    item._server = SimpleNamespace(_detach=Mock())
    return item


@pytest.mark.parametrize('detach_style', ['counter', 'transport'])
def test_actual_proactor_reset_finishes_close_once_and_restores_handler(monkeypatch, caplog, detach_style):
    from naiyue_test_agent.web import windows_disconnect as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    async def scenario():
        loop = asyncio.get_running_loop()
        original, reports = loop.get_exception_handler(), []
        previous = lambda _loop, context: reports.append(context)
        loop.set_exception_handler(previous)
        item = transport(reset())
        detached = []
        class CounterServer:
            def _detach(self): detached.append(None)
        class TransportServer:
            def _detach(self, peer): detached.append(peer)
        item._server = CounterServer() if detach_style == 'counter' else TransportServer()
        sock, server = item._sock, item._server
        try:
            with module.windows_disconnect_handler():
                loop.call_soon(item._call_connection_lost, None)
                await asyncio.sleep(0)
                assert sock.closed and item._sock is None and item._called_connection_lost
                assert item._server is None and detached == ([None] if detach_style == 'counter' else [item])
                item._call_connection_lost(None)
                assert item._protocol.connection_lost.call_count == 1 and not reports
            assert loop.get_exception_handler() is previous
        finally:
            sock.close_error = None; sock.close(); item._sock = None; item._called_connection_lost = True
            loop.set_exception_handler(original)
    asyncio.run(scenario())
    assert 'web_peer_disconnected' in caplog.text and 'PRIVATE' not in caplog.text


@pytest.mark.parametrize('case', ['other_code', 'other_callback', 'primary_error', 'cleanup_error'])
def test_other_failures_keep_original_context_and_delegate(monkeypatch, case, caplog):
    from naiyue_test_agent.web import windows_disconnect as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    async def scenario():
        loop = asyncio.get_running_loop()
        previous, reports = loop.get_exception_handler(), []
        loop.set_exception_handler(lambda _loop, context: reports.append(context))
        item = transport(reset(10053 if case == 'other_code' else 10054),
                         OSError('PRIVATE cleanup') if case == 'cleanup_error' else None)
        sock = item._sock
        if case == 'primary_error':
            item._sock.error.__context__ = RuntimeError('PRIVATE primary')
        def other(): raise reset()
        try:
            with module.windows_disconnect_handler():
                if case == 'other_callback': loop.call_soon(other)
                else: loop.call_soon(item._call_connection_lost, None)
                await asyncio.sleep(0)
                assert len(reports) == 1 and isinstance(reports[0]['exception'], ConnectionResetError)
                if case != 'other_callback': assert reports[0]['exception'] is sock.error
                if case != 'cleanup_error': assert not sock.closed
        finally:
            sock.close_error = None; sock.close(); item._sock = None; item._called_connection_lost = True
            loop.set_exception_handler(previous)
    asyncio.run(scenario())
    if case == 'cleanup_error': assert 'web_disconnect_cleanup_failed' in caplog.text
    assert 'PRIVATE' not in caplog.text


def test_unmatched_error_uses_default_handler_and_startup_failure_restores_it(monkeypatch):
    from naiyue_test_agent.web import windows_disconnect as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    async def scenario():
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        default = Mock()
        monkeypatch.setattr(loop, 'default_exception_handler', default)
        fault = RuntimeError('PRIVATE')
        context = {'message': 'synthetic', 'exception': fault}
        try:
            loop.set_exception_handler(None)
            with pytest.raises(RuntimeError) as caught:
                with module.windows_disconnect_handler():
                    loop.call_exception_handler(context)
                    raise fault
            assert caught.value is fault and loop.get_exception_handler() is None
            assert default.call_args.args == (context,)
        finally: loop.set_exception_handler(previous)
    asyncio.run(scenario())


def test_non_windows_and_later_handler_are_not_overwritten(monkeypatch):
    from naiyue_test_agent.web import windows_disconnect as module
    async def scenario():
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        try:
            monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='darwin'))
            with module.windows_disconnect_handler():
                assert loop.get_exception_handler() is previous
            monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
            later = lambda *_: None
            with module.windows_disconnect_handler(): loop.set_exception_handler(later)
            assert loop.get_exception_handler() is later
        finally: loop.set_exception_handler(previous)
    asyncio.run(scenario())


def test_real_web_lifespan_installs_handler_and_stays_available_after_reset(monkeypatch, tmp_path):
    from naiyue_test_agent.web import windows_disconnect as module
    from test_web_api import web_client
    from test_async_application import StreamingToolModel
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    async def scenario():
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        item = transport(reset())
        sock = item._sock
        try:
            async with web_client(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, app):
                assert loop.get_exception_handler() is not previous
                loop.call_soon(item._call_connection_lost, None)
                await asyncio.sleep(0)
                assert sock.closed and item._called_connection_lost
                assert (await client.get('/api/v1/scopes/chats/models')).status_code == 200
            assert loop.get_exception_handler() is previous
        finally:
            sock.close(); item._sock = None; item._called_connection_lost = True
    asyncio.run(scenario())
