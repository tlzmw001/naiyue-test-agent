import asyncio
from uuid import uuid4
import pytest
from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import config,register
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime


def test_targets_reject_credentials_and_preserve_tls_authority():
    from naiyue_test_agent.contracts.cloud_networks import ServiceTarget
    target=ServiceTarget(address='https://example.test:8443/api')
    assert target.host=='example.test' and target.port==8443 and target.tls
    assert target.path=='/api'
    for address in ['https://user:pass@example.test','http://host/?token=secret','http://host/#a','-oProxyCommand=x','http://host\n']:
        with pytest.raises(ValidationError):ServiceTarget(address=address)


def test_network_switch_is_persisted_and_blocks_admission_until_same_operation_finishes(tmp_path):
    from naiyue_test_agent.storage.cloud.networks import NetworkRepository,require_network
    from naiyue_test_agent.contracts.cloud_networks import NetworkChange,ServiceTarget
    with open_cloud_runtime(config(tmp_path)) as runtime:
        repo=NetworkRepository(runtime.database);ident='alice';cid=str(uuid4())
        change=NetworkChange(command_id=cid,expected_revision=0,slot='http',target=ServiceTarget(address='http://example.test'))
        value=repo.begin(ident,change)
        assert value['switching']['command_id']==cid
        with runtime.database.transaction() as conn:
            with pytest.raises(CloudError,match='cloud_network_switching'):require_network(conn,ident,0)
        assert repo.begin(ident,change)==value
        done=repo.finish(ident,cid)
        assert done['revision']==1 and done['slots']['http']['address']=='http://example.test'
        assert repo.finish(ident,cid)==done
        with runtime.database.transaction() as conn:
            with pytest.raises(CloudError,match='cloud_network_changed'):require_network(conn,ident,0)
            require_network(conn,ident,1)


def test_switch_rejects_background_task_but_other_users_are_independent(tmp_path):
    from naiyue_test_agent.storage.cloud.networks import NetworkRepository
    from naiyue_test_agent.contracts.cloud_networks import NetworkChange,ServiceTarget
    with open_cloud_runtime(config(tmp_path)) as runtime:
        task=register(runtime).model_copy(update={'network_id':'alice','reclaim_blockers':['background_commands']})
        runtime.registry.save_task_cas(task,expected_revision=task.revision,expected_generation=task.generation,revoked_at=0)
        repo=NetworkRepository(runtime.database)
        change=NetworkChange(command_id=str(uuid4()),expected_revision=0,slot='http',target=ServiceTarget(address='http://example.test'))
        with pytest.raises(CloudError,match='cloud_network_busy'):repo.begin('alice',change)
        assert repo.begin('bob',change)['switching']


def test_cancel_pending_switch_restores_old_target_and_fences_old_request(tmp_path):
    from naiyue_test_agent.storage.cloud.networks import NetworkRepository
    from naiyue_test_agent.contracts.cloud_networks import NetworkChange,ServiceTarget
    with open_cloud_runtime(config(tmp_path)) as runtime:
        repo=NetworkRepository(runtime.database)
        first=NetworkChange(command_id=str(uuid4()),expected_revision=0,slot='http',target=ServiceTarget(address='http://old.test'))
        repo.begin('alice',first);repo.finish('alice',first.command_id)
        change=NetworkChange(command_id=str(uuid4()),expected_revision=1,slot='http',target=ServiceTarget(address='http://new.test'))
        repo.begin('alice',change)
        state=repo.cancel('alice',change.command_id)
        assert state['slots']['http']['address']=='http://old.test' and not state['switching']
        with pytest.raises(CloudError,match='cloud_network_changed'):repo.begin('alice',change)


def test_run_reservation_atomically_rejects_switch_or_stale_route_revision(tmp_path):
    from naiyue_test_agent.storage.cloud.networks import NetworkRepository
    from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
    from naiyue_test_agent.contracts.cloud_tasks import CommandReceipt
    from naiyue_test_agent.contracts.cloud_networks import NetworkChange,ServiceTarget
    with open_cloud_runtime(config(tmp_path)) as runtime:
        task=register(runtime).model_copy(update={'network_id':'alice'})
        runtime.registry.save_task_cas(task,expected_revision=task.revision,expected_generation=task.generation,revoked_at=0)
        networks=NetworkRepository(runtime.database)
        change=NetworkChange(command_id=str(uuid4()),expected_revision=0,slot='http',target=ServiceTarget(address='http://service.test'))
        networks.begin('alice',change)
        candidate=task.model_copy(update={'revision':task.revision+1,'active_run_id':'a'*32})
        receipt=CommandReceipt(command_id=str(uuid4()),resource_id=task.task_id,operation='run:start',request_digest='b'*64,
            status='running',http_status=202,created_at=1,updated_at=1,result={'command':{'services':{'revision':0}}})
        repository=LifecycleRepository(runtime.database)
        with pytest.raises(CloudError,match='cloud_network_switching'):repository.reserve(task,candidate,receipt,runtime.settings)
        assert runtime.registry.get_task(task.task_id).active_run_id is None
        networks.finish('alice',change.command_id)
        with pytest.raises(CloudError,match='cloud_network_changed'):repository.reserve(task,candidate,receipt,runtime.settings)
        receipt=receipt.model_copy(update={'result':{'command':{'services':{'revision':1}}}})
        assert repository.reserve(task,candidate,receipt,runtime.settings)[1]
        with pytest.raises(CloudError,match='cloud_network_busy'):
            networks.begin('alice',change.model_copy(update={'command_id':str(uuid4()),'expected_revision':1}))


def test_existing_runner_receives_updated_routes_on_each_round(tmp_path):
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.cloud_runner.kernel import open_runner
    from naiyue_test_agent.contracts.cloud_networks import service_context,NetworkEndpoints
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import StreamingToolModel
    from test_runner_kernel import bootstrap,request
    async def scenario():
        root=tmp_path/'project';root.mkdir();state=tmp_path/'state';state.mkdir(mode=0o700)
        model=StreamingToolModel(replies=[AIMessage(content='first'),AIMessage(content='second')])
        endpoints=NetworkEndpoints(bind_host='172.18.0.1',http_port=21001,grpc_port=21002)
        async with open_runner(bootstrap(),root,state,catalog=ModelCatalog('A',{'A':model})) as runner:
            for revision,address in [(1,'https://old.test/api'),(2,'https://new.test/api')]:
                routes=service_context({'revision':revision,'slots':{'http':{'address':address,'enabled':True}}},endpoints)
                result=await runner.execute(request(services=routes))
                assert result['status']=='completed'
        for index,host in enumerate(('old.test','new.test')):
            instructions=[m.content for m in model.seen[index] if m.type=='system' and 'Current task service routes' in str(m.content)]
            assert host in instructions[-1]
            assert '172.18.0.1' in instructions[-1] and '21001' in instructions[-1]
            assert 'Never disable certificate verification' in instructions[-1]
        assert 'old.test' not in instructions[-1]
    asyncio.run(scenario())


def test_unbound_run_command_remains_accepted_by_existing_057_runner():
    from pydantic import Field
    from naiyue_test_agent.contracts.cloud_tasks import CloudRecord,UUIDText,TaskId,Label
    from naiyue_test_agent.contracts.cloud_models import ThinkingLevel
    from naiyue_test_agent.contracts.cloud_results import ResultPlan
    from test_runner_kernel import request
    # The strict wire consumer shipped in 0.5.7, before optional service routing.
    class ExistingRunCommand(CloudRecord):
        command_id:UUIDText
        run_id:TaskId
        message:str=Field(min_length=1,max_length=262144)
        model_id:Label
        thinking_level:ThinkingLevel='model_default'
        result_plan:ResultPlan|None=None
    command=request()
    accepted=ExistingRunCommand.model_validate(command.model_dump(mode='json'))
    assert accepted.command_id==command.command_id and accepted.message==command.message
    routed=request(services={'revision':1,'slots':{}})
    assert routed.model_dump(mode='json')['services']['revision']==1


def test_unbound_task_view_omits_new_optional_network_field():
    from cloud_helpers import task_record
    task=task_record(str(uuid4()))
    # Existing 0.5.7 clients forbid unknown task-view fields even when null.
    assert 'network_id' not in task.model_dump(mode='json')
    assert task.model_copy(update={'network_id':'alice'}).model_dump(mode='json')['network_id']=='alice'
