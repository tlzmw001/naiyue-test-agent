"""Launch entry tests against the real TCP service with fixture settings only."""
from contextlib import contextmanager
from naiyue_test_agent.service.client import ServiceClient
from terminal_service_helpers import TerminalService


@contextmanager
def isolated_web_launch(monkeypatch, *, observe=None):
    from naiyue_test_agent.web import __main__ as entry
    from naiyue_test_agent.service import app as service_module
    servers, inspection_failures = [], []
    async def connect(options):
        runtime = entry.load_settings(options.settings_path)
        patch.setattr(service_module, 'load_settings', lambda path: runtime)
        server = TerminalService(options)
        server.__enter__()
        servers.append(server)
        client = await ServiceClient.connect(server.options)
        if observe is not None:
            close = client.aclose
            async def inspect_close():
                try:
                    await observe(client, server)
                except BaseException as error:
                    inspection_failures.append(error)
                finally:
                    await close()
            client.aclose = inspect_close
        return client
    with monkeypatch.context() as patch:
        patch.setattr(entry, 'ensure_service', connect)
        try:
            yield servers
        finally:
            for server in reversed(servers):
                server.__exit__(None, None, None)
            if inspection_failures:
                raise inspection_failures[0]
