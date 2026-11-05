import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from lifecycle_helpers import config
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud_local.networks import ServiceNetworks
from naiyue_test_agent.cloud_files.profiles import ProfileBook,CloudProfile
from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
from naiyue_test_agent.storage.cloud.networks import NetworkRepository
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_networks import ServiceTarget,NetworkEndpoints


@pytest.mark.parametrize('slot',['http','extra'])
def test_lost_finish_ack_retries_original_and_disabled_intent_survives_restart(monkeypatch,tmp_path,slot):
    from naiyue_test_agent.application.cloud_local import networks as module
    events=[]
    class Process:
        def __init__(self,args,timeout):self.alive=True;self.ready=True;events.append(('start',args))
        def close(self):self.alive=False;events.append(('stop',))
    monkeypatch.setattr(module,'ReverseProcess',Process)
    monkeypatch.setattr(module,'probe_target',lambda *a:{'tcp_reachable':True})
    with open_cloud_runtime(config(tmp_path)) as runtime:
        repo=NetworkRepository(runtime.database)
        endpoints=dict(bind_host='172.18.0.1',ports={'http':21001,'grpc':21002,'extra':21003})
        class Network(ServiceNetworks):
            lose=True
            def start(self):pass
            async def request(self,profile,method,suffix='',**kwargs):
                if method=='GET':return dict(state=repo.read('alice'),endpoints=endpoints)
                if suffix=='/prepare':return repo.begin('alice',kwargs['json'])
                if suffix=='/cancel':return repo.cancel('alice',kwargs['json']['command_id'])
                if suffix=='/finish':
                    result=repo.finish('alice',kwargs['json']['command_id'])
                    if self.lose:self.lose=False;raise CloudError('cloud_connection_failed',503)
                    return result
                return {'tcp_reachable':True}
        profile=CloudProfile(profile_id='work',network_id='alice',ssh_host='shared',connection_file=tmp_path/'connection.json',template_ids=['test'],default_template='test')
        manager=SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook(profiles=[profile]))
        async def scenario():
            service=Network(manager);target=ServiceTarget(address='https://service.test' if slot=='http' else 'tcp://cache.test:6379',name='' if slot=='http' else '缓存')
            with pytest.raises(CloudError,match='cloud_connection_failed'):await service.change('work',slot,target,0)
            pending=service.state['work']['pending'];assert pending
            await service.change('work',slot,target,0)
            assert repo.read('alice')['revision']==1
            assert len([v for v in events if v[0]=='start'])==1
            assert service.state['work']['pending'] is None
            await service.change('work',slot,target.model_copy(update={'enabled':False}),1)
            await service.close()
            restarted=Network(manager)
            assert not restarted.state['work']['slots'][slot]['enabled']
            await restarted._connect(profile,slot,target.model_copy(update={'enabled':False}),NetworkEndpoints(**endpoints))
            assert not restarted.processes
            await restarted.close()
        asyncio.run(scenario())


def test_supervisor_keeps_grpc_reconnect_independent_from_failed_http(tmp_path):
    profile=CloudProfile(profile_id='work',network_id='alice',ssh_host='shared',connection_file=tmp_path/'connection.json',template_ids=['test'],default_template='test')
    manager=SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook(profiles=[profile]))
    endpoints=dict(bind_host='172.18.0.1',http_port=21001,grpc_port=21002)
    async def scenario():
        service=ServiceNetworks(manager);calls=[]
        service.state={'work':dict(network_id='alice',endpoints=endpoints,pending=None,slots={
            'http':{'address':'http://offline.test','enabled':True},'grpc':{'address':'grpc://healthy.test:443','enabled':True}})}
        async def request(*a,**kw):
            asyncio.get_running_loop().call_soon(service.stopping.set)
            return dict(endpoints=endpoints)
        async def connect(profile,slot,*a):
            calls.append(slot)
            if slot=='http':raise CloudError('cloud_target_unreachable',503)
        service.request=request;service._connect=connect
        await service._maintain()
        assert calls==['http','grpc']
    asyncio.run(scenario())


def test_pending_http_failure_does_not_restore_old_http_or_block_grpc(tmp_path):
    profile=CloudProfile(profile_id='work',network_id='alice',ssh_host='shared',connection_file=tmp_path/'connection.json',template_ids=['test'],default_template='test')
    manager=SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook(profiles=[profile]))
    endpoints=dict(bind_host='172.18.0.1',http_port=21001,grpc_port=21002)
    async def scenario():
        service=ServiceNetworks(manager);calls=[]
        service.state={'work':dict(network_id='alice',endpoints=endpoints,pending={'slot':'http'},slots={
            'http':{'address':'http://old.test','enabled':True},'grpc':{'address':'grpc://healthy.test:443','enabled':True}})}
        async def request(*a,**kw):
            asyncio.get_running_loop().call_soon(service.stopping.set)
            return dict(endpoints=endpoints)
        async def apply(*a):raise CloudError('cloud_target_unreachable',503)
        async def connect(profile,slot,*a):calls.append(slot)
        service.request=request;service._connect=connect;service._apply=apply
        await service._maintain()
        assert calls==['grpc']
        assert service.status['work','http']=='cloud_target_unreachable'
    asyncio.run(scenario())


@pytest.mark.parametrize('operation',['change','cancel'])
def test_failed_intent_save_never_publishes_new_operation(tmp_path,operation):
    from copy import deepcopy
    profile=CloudProfile(profile_id='work',network_id='alice',ssh_host='shared',connection_file=tmp_path/'connection.json',template_ids=['test'],default_template='test')
    manager=SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook(profiles=[profile]))
    async def scenario():
        service=ServiceNetworks(manager);service.start=lambda:None;calls=[]
        if operation=='cancel':service.state={'work':{'pending':{'slot':'http'},'network_id':'alice','slots':{}}}
        before=deepcopy(service.state)
        async def request(profile,method,*a,**kw):
            calls.append(method)
            return dict(state={'switching':None},endpoints={'bind_host':'172.18.0.1','ports':{'http':21001}})
        async def fail_save():raise OSError('disk_full')
        service.request=request;service.save=fail_save
        with pytest.raises(OSError,match='disk_full'):
            if operation=='change':await service.change('work','http',ServiceTarget(address='http://target.test'),0)
            else:await service.cancel('work')
        assert service.state==before
        assert all(method=='GET' for method in calls)
    asyncio.run(scenario())


def test_shutdown_attempts_both_tunnels_when_one_close_fails(tmp_path):
    manager=SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook())
    async def scenario():
        service=ServiceNetworks(manager);closed=[]
        class Process:
            def __init__(self,slot):self.slot=slot
            def close(self):
                closed.append(self.slot)
                if self.slot=='http':raise CloudError('cloud_ssh_stop_failed',503)
        service.processes={('work',s):Process(s) for s in ('http','grpc')}
        with pytest.raises(CloudError,match='cloud_ssh_stop_failed'):await service.close()
        assert closed==['http','grpc']
        assert list(service.processes)==[('work','http')]
    asyncio.run(scenario())
