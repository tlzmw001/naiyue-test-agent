"""Two private listeners sharing one owner, SDK loop and ordered shutdown."""
import asyncio
from contextlib import ExitStack, contextmanager
import logging
import signal
import socket
import sqlite3
from urllib.parse import urlsplit

import uvicorn

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from .app import create_cloud_apps
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.work import cloud_work

logger=logging.getLogger(__name__)


async def _maintain_inputs(runtime,stopping):
    while not stopping.is_set():
        try:await runtime.inputs.sweep_expired()
        except (CloudError,OSError,sqlite3.Error) as error:
            logger.error('cloud_input_maintenance_failed error_type=%s',type(error).__name__)
        try:await asyncio.wait_for(stopping.wait(),runtime.settings.input_limits.sweep_interval_seconds)
        except TimeoutError:pass


async def _maintain_resources(runtime, stopping):
    while not stopping.is_set():
        try:
            await cloud_work(runtime,runtime.results.retention.sweep)
            await runtime.lifecycle.sweep_idle(stopping=stopping)
            await cloud_work(runtime, lambda: runtime.resources.disk(refresh=True))
        except (CloudError, OSError, sqlite3.Error) as error:
            logger.error('cloud_resource_maintenance_failed error_type=%s', type(error).__name__)
        try:
            await asyncio.wait_for(stopping.wait(), runtime.settings.resource_limits.maintenance_interval_seconds)
        except TimeoutError:
            pass


class _Server(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The owner installs one set of handlers for both server instances.
        yield


async def _serve(runtime):
    async with open_cloud_backend(runtime):
        apps = create_cloud_apps(runtime)
        servers, sockets = [], []
        stopping = asyncio.Event()
        loop = asyncio.get_running_loop()
        previous = {}
        with ExitStack() as stack:
            for app, authority in zip(apps, (runtime.settings.owner_listen, runtime.settings.task_listen)):
                url = urlsplit('http://' + authority)
                family = socket.AF_INET6 if ':' in url.hostname else socket.AF_INET
                listener = socket.socket(family)
                stack.callback(listener.close)
                listener.set_inheritable(False)
                listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                try:listener.bind((url.hostname, url.port))
                except OSError as error:
                    logger.error('cloud_listener_bind_failed errno=%s',error.errno)
                    raise CloudError('cloud_listener_unavailable',503) from error
                listener.listen()
                config = uvicorn.Config(app, host=url.hostname, port=url.port, access_log=False, log_config=None,
                    ws_max_size=runtime.settings.max_frame_bytes,ws_max_queue=16)
                servers.append(_Server(config))
                sockets.append(listener)
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                loop.add_signal_handler(signum, stopping.set)
            jobs = [asyncio.create_task(server.serve(sockets=[listener])) for server, listener in zip(servers, sockets)]
            jobs.append(asyncio.create_task(_maintain_inputs(runtime,stopping)))
            jobs.append(asyncio.create_task(_maintain_resources(runtime,stopping)))
            waiter = asyncio.create_task(stopping.wait())
            try:
                done, _ = await asyncio.wait([*jobs, waiter], return_when=asyncio.FIRST_COMPLETED)
                for job in done:
                    if job is not waiter: job.result()
            finally:
                stopping.set()
                for server in servers: server.should_exit = True
                await runtime.observations.aclose()
                waiter.cancel()
                await asyncio.gather(*jobs, waiter, return_exceptions=True)
                for signum, handler in previous.items():
                    loop.remove_signal_handler(signum)
                    signal.signal(signum, handler)


def serve_cloud(settings):
    for name in ('httpx','httpcore','httpx2','httpcore2'):
        logging.getLogger(name).setLevel(logging.ERROR)
    with open_cloud_runtime(settings) as runtime:
        asyncio.run(_serve(runtime))
    return 0
