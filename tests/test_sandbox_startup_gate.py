"""A basic image import cannot be mistaken for production startup evidence."""
import pytest


def report():
    return dict(hello=True,task_identity=dict(uid=1000,gid=1000,groups=[],
        caps=dict(CapEff=0,CapPrm=0,CapInh=0,CapAmb=0),no_new_privs=1),
        isolation=dict(uid=1000,journal_readwrite=False,socket_connect=False,controller_environ_read=False),
        bash=True,aitest=True,stopped=True,owned_resources_removed=True)


def test_startup_gate_requires_the_complete_production_path():
    from scripts.verify_sandbox_startup import verify_report
    verify_report(report())


@pytest.mark.parametrize('field',['hello','task_identity','isolation','bash','aitest','stopped','owned_resources_removed'])
def test_startup_gate_rejects_missing_or_unverified_boundary(field):
    from scripts.verify_sandbox_startup import verify_report
    value=report();value.pop(field)
    with pytest.raises(ValueError):verify_report(value)


def test_direct_startup_gate_grants_first_start_only_for_ssh(tmp_path,monkeypatch):
    import asyncio
    from scripts import verify_sandbox_startup as gate
    from naiyue_test_agent.sandbox_settings import SandboxSettings
    original=RuntimeError('stop-after-handshake');starts=[];cleanup=[]
    class CLI:
        def __init__(self,*args):pass
        def preflight(self,*args):return 'D',{'Id':'sha256:fixture'}
    class Engine:
        def __init__(self,*args):pass
        def create(self,row,save):
            row=row.changed(container_id='c'*64);save(row);return row
        def handshake(self,row,*,start=False):starts.append(start);raise original
        def action(self,row,action):cleanup.append(action)
    class Transport:
        def __init__(self,*args):pass
        async def aclose(self):return None
    monkeypatch.setattr(gate,'DockerCLI',CLI);monkeypatch.setattr(gate,'DockerEnvironment',Engine)
    monkeypatch.setattr(gate,'SandboxTransport',Transport)
    for ssh in (None,{'host':'fixture'}):
        with pytest.raises(RuntimeError) as caught:
            asyncio.run(gate.scenario(tmp_path,SandboxSettings(image='fixture',ssh=ssh),{}))
        assert caught.value is original
    assert starts==[False,True] and cleanup==['destroy','destroy']
