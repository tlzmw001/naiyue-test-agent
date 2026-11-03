"""A private listener must restart after actively closing accepted connections."""
import asyncio
from contextlib import asynccontextmanager, nullcontext
import socket
from types import SimpleNamespace


def test_private_listeners_rebind_after_active_close(tmp_path,monkeypatch):
    import naiyue_test_agent.service.cloud.server as module
    addresses=[]
    @asynccontextmanager
    async def backend(runtime):yield
    class Server:
        def __init__(self,config):self.should_exit=False
        async def serve(self,sockets):
            listener=sockets[0];address=listener.getsockname();addresses.append(address)
            client=socket.socket();client.connect(address)
            accepted,_=listener.accept()
            try:
                accepted.shutdown(socket.SHUT_WR);accepted.close()
                assert client.recv(1)==b''
            finally:client.close()
    monkeypatch.setattr(module,'open_cloud_backend',backend)
    monkeypatch.setattr(module,'create_cloud_apps',lambda runtime:(None,None))
    monkeypatch.setattr(module,'_Server',Server)
    async def scenario():
        sweeps=[];observation_closes=[];resource_sweeps=[];measurements=[];result_sweeps=[]
        async def sweep():sweeps.append(True)
        async def resources(**kwargs):resource_sweeps.append(True)
        def measure(**kwargs):measurements.append(True)
        def results():result_sweeps.append(True)
        async def close_observations():observation_closes.append(True)
        settings=SimpleNamespace(owner_listen='127.0.0.1:0',task_listen='127.0.0.1:0',max_frame_bytes=4096,
            input_limits=SimpleNamespace(sweep_interval_seconds=60.0),
            resource_limits=SimpleNamespace(maintenance_interval_seconds=60.0))
        runtime=SimpleNamespace(settings=settings,inputs=SimpleNamespace(sweep_expired=sweep),
            observations=SimpleNamespace(aclose=close_observations),results=SimpleNamespace(retention=SimpleNamespace(sweep=results)),
            lifecycle=SimpleNamespace(sweep_idle=resources),resources=SimpleNamespace(disk=measure),operation=nullcontext)
        await module._serve(runtime)
        assert len(addresses)==2
        settings.owner_listen='127.0.0.1:'+str(addresses[0][1])
        settings.task_listen='127.0.0.1:'+str(addresses[1][1])
        await module._serve(runtime)
        assert addresses[2:]==addresses[:2]
        assert len(sweeps)==2
        assert len(resource_sweeps)==2 and len(measurements)==2
        assert len(observation_closes)==2 and len(result_sweeps)==2
    asyncio.run(scenario())
