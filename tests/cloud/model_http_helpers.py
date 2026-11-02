"""Real loopback HTTP servers for native SDK/relay proofs, using project Uvicorn."""
import asyncio
from contextlib import asynccontextmanager
import socket

import uvicorn

from naiyue_test_agent.service.cloud.server import _Server
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps


@asynccontextmanager
async def live_app(app):
    listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    listener.bind(('127.0.0.1',0));listener.listen();listener.set_inheritable(False)
    ready=asyncio.Event()
    class Server(_Server):
        async def startup(self,sockets):
            await super().startup(sockets);ready.set()
    server=Server(uvicorn.Config(app,lifespan='off',access_log=False,log_config=None))
    job=asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await asyncio.wait_for(ready.wait(),5)
        assert server.started
        yield 'http://127.0.0.1:'+str(listener.getsockname()[1])
    finally:
        server.should_exit=True
        await asyncio.wait_for(job,10)
        listener.close()


@asynccontextmanager
async def cloud_task(config):
    """Third repeated native-network setup shares one resource owner in tests."""
    with open_cloud_runtime(config) as runtime:
        owner,task=create_cloud_apps(runtime)
        async with live_app(task) as url:
            # Only the fixture substitutes loopback for deployment's bridge.
            runtime.settings=config.model_copy(update={'task_listen':url.removeprefix('http://')})
            try:yield runtime,owner,url
            finally:await runtime.models.aclose()
