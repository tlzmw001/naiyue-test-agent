"""Run the real service on TCP while keeping fake model/tool injection server-side."""
import asyncio
from contextlib import contextmanager
from threading import Event, Thread
import socket
import time

import uvicorn
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.server import ServiceServer
from naiyue_test_agent.cli.terminal_client import TerminalClient


class TerminalService:
    def __init__(self, options, *, application_factory=None):
        self._ready, self._error = Event(), None
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(128)
        self.options = options.model_copy(update={'port': self.listener.getsockname()[1]})
        self.app = create_service_app(self.options, application_factory=application_factory)
        self.server = ServiceServer(uvicorn.Config(self.app, log_level='critical', access_log=False))
        self.thread = Thread(target=self._serve, name='terminal-test-service')

    def _serve(self):
        async def serve():
            self.loop = asyncio.get_running_loop()
            self._ready.set()
            await self.server.serve(sockets=[self.listener])
        try:
            asyncio.run(serve())
        except BaseException as error:
            self._error = error
            self._ready.set()

    def __enter__(self):
        self.thread.start()
        self._ready.wait(10)
        deadline = time.monotonic() + 10
        while not self.server.started:
            if self._error: raise self._error
            if time.monotonic() > deadline: raise AssertionError('Test service did not start')
            time.sleep(0.01)
        return self

    def __exit__(self, *args):
        self.server.should_exit = True
        self.thread.join(10)
        self.listener.close()
        assert not self.thread.is_alive(), 'Test service did not shut down'
        if self._error: raise self._error

    def client(self):
        return TerminalClient(self.options)

    def inspect(self, operation):
        async def run():
            value = operation(self.app.state.application)
            return await value if hasattr(value, '__await__') else value
        return asyncio.run_coroutine_threadsafe(run(), self.loop).result(10)


@contextmanager
def isolated_terminal_launch(monkeypatch):
    """Legacy entry scenarios get a real service per invocation, closed before DB assertions.

    Trust is explicit test input. Shared-service reconnect/concurrency has separate
    TCP tests; this harness must not reintroduce an Application inside the client.
    """
    from naiyue_test_agent.cli import chat_startup
    servers = []
    def client(options):
        from naiyue_test_agent.cli import chat
        from naiyue_test_agent.service import app as service_module
        runtime = chat.load_settings()
        patch.setattr(service_module, 'load_settings', lambda path: runtime)
        server = TerminalService(options)
        server.__enter__()
        servers.append(server)
        return server.client()
    with monkeypatch.context() as patch:
        patch.setattr(chat_startup, 'TerminalClient', client)
        try:
            yield servers
        finally:
            for server in reversed(servers):
                server.__exit__(None, None, None)


class HostProbe:
    """Test-side inspection runs on the real service owner loop."""
    def __init__(self, host):
        self.host, self.loop = host, asyncio.get_running_loop()

    def inspect(self, operation):
        async def run():
            value = operation(self.host)
            return await value if hasattr(value, '__await__') else value
        return asyncio.run_coroutine_threadsafe(run(), self.loop).result(10)


def capture_hosts(monkeypatch, observe=None):
    from naiyue_test_agent.application.projects import host_manager
    original = host_manager.assemble_host
    captured = []
    async def assemble(**options):
        host = await original(**options)
        probe = HostProbe(host)
        captured.append(probe)
        if observe is not None:
            observe(host, options)
        return host
    monkeypatch.setattr(host_manager, 'assemble_host', assemble)
    return captured


@contextmanager
def terminal_runtime(tmp_path, *, tools=(), settings=None, initial_active_tools=None,
                     disk=False, root=None, settings_path=None):
    from contextlib import asynccontextmanager
    from naiyue_test_agent.application.service_application import open_service_application
    from naiyue_test_agent.config_paths import StorageSettings
    from naiyue_test_agent.service.settings import ServiceOptions
    from naiyue_test_agent.settings import AppSettings
    data_dir = tmp_path.parent / (tmp_path.name + '-service-data')
    runtime = (settings or AppSettings()).model_copy(update={'storage': StorageSettings(data_dir=data_dir)})
    options = ServiceOptions(data_dir=data_dir, config_path=tmp_path / 'models.json',
        settings_path=settings_path or tmp_path / 'settings.toml', frontend_dist=tmp_path / 'assets')
    @asynccontextmanager
    async def factory():
        async with open_service_application(options.config_path, tools=tools, settings=runtime,
                system_prompt='', response_format=None, initial_active_tools=initial_active_tools) as app:
            yield app
    with TerminalService(options, application_factory=factory) as server:
        client = server.client()
        try:
            if disk:
                client.bind('chats')
            else:
                client.lease(root)
            client.request('GET', '/models')
            host = server.app.state.scopes.services[client.scope_id].application
            probe = HostProbe.__new__(HostProbe)
            probe.host, probe.loop = host, server.loop
            yield client, probe
        finally:
            client.close()


class HostedTerminal:
    def __init__(self, tmp_path, **options):
        self.manager = terminal_runtime(tmp_path, **options)
        self.client, self.probe = self.manager.__enter__()
        self.host = self.probe.host

    def inspect(self, operation):
        return self.probe.inspect(operation)

    def close(self):
        self.manager.__exit__(None, None, None)


def approve(client, session_id, choices):
    batch, = client.pending_approvals(session_id)
    return client.approve(session_id, {'approval_batch_id': batch['approval_batch_id'],
        'origin_run_id': batch['origin_run_id'], 'decisions': [
            {'approval_id': row['approval_id'], 'decision': decision}
            for row, decision in zip(batch['items'], choices, strict=True)]})


def terminal_lifecycle(monkeypatch):
    facts = []
    watch, close = TerminalClient.watch, TerminalClient.close
    def observe(self, *args, **kwargs):
        result = watch(self, *args, **kwargs)
        facts.append('subscribe')
        return result
    def finish(self):
        if not self._closed and self._watcher is not None:
            try:
                return close(self)
            finally:
                facts.append('unsubscribe')
        return close(self)
    monkeypatch.setattr(TerminalClient, 'watch', observe)
    monkeypatch.setattr(TerminalClient, 'close', finish)
    return facts
