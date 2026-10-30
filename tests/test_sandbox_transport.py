"""Production transport validates on connection, shares it, and closes by binding."""
import asyncio

from test_docker_environment import record


def test_warm_requests_do_not_revalidate_or_spawn_and_other_binding_survives(tmp_path,monkeypatch):
    from naiyue_test_agent.execution.sandbox_transport import SandboxTransport
    from naiyue_test_agent.sandbox_profiles import SandboxConfiguration
    counts={'validates':0,'opens':0};channels=[]
    def validate(self,row,**kwargs):counts['validates']+=1
    class Channel:
        failed=None
        def __init__(self):self.closed=False
        async def request(self,operation,params,**kwargs):return dict(operation=operation,params=params)
        async def close(self,*,deadline=None):self.closed=True
    async def open(*args,**kwargs):
        counts['opens']+=1;channel=Channel();channels.append(channel);return channel
    monkeypatch.setattr('naiyue_test_agent.execution.docker_environment.DockerEnvironment.validate',validate)
    monkeypatch.setattr('naiyue_test_agent.execution.docker_cli.DockerCLI.command',lambda *_:['unused'])
    monkeypatch.setattr('naiyue_test_agent.execution.sandbox_channel.SandboxChannel.open',open)
    transport=SandboxTransport(tmp_path,SandboxConfiguration())
    one=record();two=one.changed(binding_id='other',session_id='other-session',container_id='d'*64)
    async def run():
        assert await transport.arequest(one,'read',{})=={'operation':'read','params':{}}
        await asyncio.gather(*(transport.arequest(one,'read',{'i':i}) for i in range(20)))
        await transport.arequest(two,'read',{})
        assert counts=={'validates':2,'opens':2}
        await transport.ainvalidate(one.binding_id)
        assert channels[0].closed and not channels[1].closed
        await transport.arequest(two,'read',{})
        await transport.aclose()
        assert all(channel.closed for channel in channels)
    asyncio.run(run())
    assert not transport.runtime.thread.is_alive()
