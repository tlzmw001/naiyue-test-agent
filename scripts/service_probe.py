"""Probe-owned TCP service; it never connects a diagnostic tool to a user's daemon."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import time

import uvicorn
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.cli.terminal_client import TerminalClient
from naiyue_test_agent.config_paths import StorageSettings
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.server import ServiceServer
from naiyue_test_agent.service.settings import ServiceOptions
from naiyue_test_agent.settings import AppSettings


@asynccontextmanager
async def terminal_probe(config_path, *, lease=True, **assembly):
    with TemporaryDirectory(prefix='naiyue-terminal-probe-') as directory, socket.socket() as listener:
        root = Path(directory)
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        options = ServiceOptions(data_dir=root / 'data', config_path=config_path,
            settings_path=root / 'settings.toml', frontend_dist=root / 'assets',
            port=listener.getsockname()[1])
        @asynccontextmanager
        async def factory():
            async with open_service_application(options.config_path,
                    settings=AppSettings(storage=StorageSettings(data_dir=options.data_dir)), **assembly) as app:
                yield app
        app = create_service_app(options, application_factory=factory)
        server = ServiceServer(uvicorn.Config(app, access_log=False, log_level='critical'))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        client = None
        try:
            deadline = time.monotonic() + options.startup_timeout
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError('probe_service_stopped')
                if time.monotonic() >= deadline:
                    raise TimeoutError('probe_service_start_timeout')
                await asyncio.sleep(0.01)
            client = await asyncio.to_thread(TerminalClient, options)
            if lease:
                await asyncio.to_thread(client.lease, None)
            yield client
        finally:
            try:
                if client is not None:
                    await asyncio.to_thread(client.close)
            finally:
                server.should_exit = True
                await asyncio.wait_for(asyncio.shield(serving), 10)
