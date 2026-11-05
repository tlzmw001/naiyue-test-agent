import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_networks import NetworkEndpoints,NetworkChange,ServiceTarget,service_context
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_many_ports_are_protocol_independent_and_keep_route_metadata():
    endpoints=NetworkEndpoints(bind_host='172.18.0.1',ports={'api':21001,'other':21002,'cache':21003})
    slots={}
    for slot,address in [('api','grpcs://rpc.test:443'),('other','https://web.test/base'),('cache','tcp://cache.test:6379')]:
        target=ServiceTarget(address=address,name=slot+' service')
        change=NetworkChange(command_id=str(uuid4()),expected_revision=0,slot=slot,target=target)
        slots[slot]=change.target.model_dump()
    routes=service_context({'revision':2,'slots':slots},endpoints)
    assert routes['slots']['cache']['connect_port']==21003
    assert routes['slots']['api']['server_name']=='rpc.test'
    assert routes['slots']['other']['address']=='https://web.test/base'
    assert routes['slots']['cache']['name']=='cache service'
    assert not routes['slots']['cache']['tls']


def test_legacy_endpoints_normalize_without_parallel_fields():
    old=NetworkEndpoints(bind_host='172.18.0.1',http_port=21001,grpc_port=21002)
    assert old.model_dump()=={'bind_host':'172.18.0.1','ports':{'http':21001,'grpc':21002}}
    with pytest.raises(ValidationError):NetworkEndpoints(bind_host='172.18.0.1',ports={'x':21003},http_port=21001,grpc_port=21002)


@pytest.mark.parametrize('ports',[{}, {'a':21001,'b':21001},{'../bad':21001},{'test':True},{'test':80}])
def test_invalid_port_lists_are_rejected(ports):
    with pytest.raises(ValidationError):NetworkEndpoints(bind_host='172.18.0.1',ports=ports)


@pytest.mark.parametrize('address',['tcp://host','tcp://host:90/path','tls://user:pw@host:90','tcp://host:90?q=secret','udp://host:90'])
def test_generic_targets_reject_unsafe_or_unsupported_addresses(address):
    with pytest.raises(ValidationError):ServiceTarget(address=address)


def test_removed_enabled_port_does_not_silently_disappear():
    endpoints=NetworkEndpoints(bind_host='172.18.0.1',ports={'api':21001})
    with pytest.raises(CloudError,match='cloud_network_endpoint_missing'):
        service_context({'revision':1,'slots':{'removed':{'address':'tcp://host:90','enabled':True}}},endpoints)


def test_local_profile_and_port_rejected_before_mutation(tmp_path):
    from naiyue_test_agent.application.cloud_local.networks import ServiceNetworks
    from naiyue_test_agent.cloud_files.profiles import ProfileBook,CloudProfile
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    profile=CloudProfile(profile_id='mine',network_id='alice',ssh_host='shared',connection_file=tmp_path/'conn',template_ids=['test'],default_template='test')
    manager=SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook(profiles=[profile]))
    async def scenario():
        service=ServiceNetworks(manager);service.start=lambda:None;calls=[]
        async def request(profile,method,*args,**kwargs):
            calls.append(method);return {'endpoints':{'bind_host':'172.18.0.1','ports':{'api':21001}},'state':{'switching':None}}
        service.request=request
        with pytest.raises(CloudError,match='cloud_profile_not_found'):await service.read('bob')
        assert calls==[]
        with pytest.raises(CloudError,match='cloud_network_endpoint_missing'):await service.change('mine','other',ServiceTarget(address='tcp://host:90'),0)
        assert calls==['GET'] and service.state=={} and not service.path.exists()
    asyncio.run(scenario())


def test_endpoint_extension_keeps_existing_routes_but_rejects_rebinding():
    old=NetworkEndpoints(bind_host='172.18.0.1',ports={'api':21001})
    expanded=NetworkEndpoints(bind_host='172.18.0.1',ports={'api':21001,'cache':21002})
    assert expanded.extends(old)
    assert not old.extends(expanded)
    assert not NetworkEndpoints(bind_host='172.18.0.1',ports={'api':21003}).extends(old)


def test_old_pending_and_names_survive_sidecar_normalization(tmp_path):
    from naiyue_test_agent.cloud_runner.state import write_json
    from naiyue_test_agent.application.cloud_local.networks import ServiceNetworks
    from naiyue_test_agent.cloud_files.profiles import ProfileBook
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    pending={'command_id':str(uuid4()),'expected_revision':3,'slot':'http','target':{'address':'https://old.test','enabled':True}}
    write_json(tmp_path/'profiles.networks.json',{'schema':1,'profiles':{'mine':{'network_id':'alice','endpoints':{'bind_host':'172.18.0.1','http_port':21001,'grpc_port':21002},'pending':pending,'slots':{}}}})
    service=ServiceNetworks(SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook()))
    assert service.state['mine']['endpoints']['ports']=={'http':21001,'grpc':21002}
    assert service.state['mine']['pending']==pending
    assert NetworkChange.model_validate(pending).model_dump()==pending


def test_owner_api_checks_assigned_entries_and_auth(tmp_path):
    import httpx
    from lifecycle_helpers import config
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.service.cloud.app import create_cloud_apps
    from naiyue_test_agent.storage.cloud.networks import NetworkRepository
    async def scenario():
        settings=config(tmp_path).model_copy(update={'networks':{'alice':NetworkEndpoints(bind_host='172.18.0.1',ports={'one':21001,'two':21002,'third':21003})}})
        with open_cloud_runtime(settings) as r:
            owner,_=create_cloud_apps(r)
            headers={'Authorization':'Bearer '+r.settings.owner_token.get_secret_value(),'Origin':r.settings.origin,'X-Cloud-Protocol':'1'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin) as client:
                url='/api/cloud/v1/networks/alice'
                assert (await client.get(url)).status_code==401
                body={'command_id':str(uuid4()),'expected_revision':0,'slot':'unknown','target':{'address':'tcp://host:90'}}
                rejected=await client.post(url+'/prepare',json=body,headers=headers)
                assert rejected.status_code==422,rejected.text
                assert not NetworkRepository(r.database).read('alice')['switching']
                body['slot']='third'
                accepted=await client.post(url+'/prepare',json=body,headers=headers)
                assert accepted.status_code==200,accepted.text
                finished=await client.post(url+'/finish',json={'command_id':body['command_id']},headers=headers)
                assert finished.status_code==200,finished.text
                assert finished.json()['slots']['third']['address']=='tcp://host:90'
    asyncio.run(scenario())


@pytest.mark.parametrize('slot',['test','cancel','constructor'])
def test_local_entry_route_cannot_collide_with_actions(slot):
    import httpx
    from contextlib import nullcontext
    from fastapi import FastAPI,APIRouter
    from naiyue_test_agent.service.cloud_network_routes import register
    from naiyue_test_agent.service.cloud_tasks import body
    calls=[]
    class Networks:
        async def change(self,profile,entry,target,revision):
            calls.append((profile,entry,target.address,revision));return {'changed':entry}
    manager=SimpleNamespace(networks=Networks(),operation=nullcontext)
    app=FastAPI();app.state.web_settings=SimpleNamespace(max_frame_bytes=1048576);router=APIRouter();register(router,lambda request:manager,body);app.include_router(router)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await client.post('/cloud-networks/mine/entries/'+slot,json={'target':{'address':'tcp://host:90'},'expected_revision':0})
            assert response.status_code==200,response.text
            assert calls==[('mine',slot,'tcp://host:90',0)]
    asyncio.run(scenario())


def test_change_rejects_rebinding_before_saving_or_stopping(tmp_path):
    from copy import deepcopy
    from naiyue_test_agent.application.cloud_local.networks import ServiceNetworks
    from naiyue_test_agent.cloud_files.profiles import ProfileBook,CloudProfile
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    profile=CloudProfile(profile_id='mine',network_id='alice',ssh_host='shared',connection_file=tmp_path/'conn',template_ids=['test'],default_template='test')
    manager=SimpleNamespace(settings=CloudTaskSettings(profiles_file=tmp_path/'profiles.json'),book=ProfileBook(profiles=[profile]))
    async def scenario():
        service=ServiceNetworks(manager);service.start=lambda:None;calls=[]
        service.state={'mine':{'network_id':'alice','endpoints':{'bind_host':'172.18.0.1','ports':{'api':21001}},'slots':{},'pending':None}}
        before=deepcopy(service.state)
        async def request(*args,**kwargs):
            calls.append(args[1]);return {'endpoints':{'bind_host':'172.18.0.1','ports':{'api':21009}},'state':{'switching':None}}
        async def apply(*args):calls.append('apply')
        service.request=request;service._apply=apply
        with pytest.raises(CloudError,match='cloud_network_identity_changed'):await service.change('mine','api',ServiceTarget(address='tcp://host:90'),0)
        assert calls==['GET'] and service.state==before and not service.path.exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('address,port,tls', [('tcp://host:6379',6379,False),('tls://host:6380',6380,True),('grpcs://host:443',443,True)])
def test_any_entry_can_use_generic_or_grpc_tls(address,port,tls):
    target=ServiceTarget(address=address,name='目标')
    change=NetworkChange(command_id=str(uuid4()),expected_revision=0,slot='orders',target=target)
    assert change.target.port==port and change.target.tls==tls


def test_global_port_collision_and_size_limit_still_apply(tmp_path):
    from lifecycle_helpers import config
    from naiyue_test_agent.application.cloud.settings import CloudSettings
    raw=config(tmp_path).model_dump()
    raw['networks']={'alice':{'bind_host':'172.18.0.1','ports':{'a':21001}},'bob':{'bind_host':'172.18.0.1','ports':{'b':21001}}}
    with pytest.raises(ValidationError,match='duplicate_network_endpoint'):CloudSettings.model_validate(raw)
    with pytest.raises(ValidationError):NetworkEndpoints(bind_host='172.18.0.1',ports={f'entry{i}':21000+i for i in range(65)})
