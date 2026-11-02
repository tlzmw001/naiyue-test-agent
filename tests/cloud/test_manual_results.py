"""Local result commands keep original identities through pre-admission network loss."""
import asyncio,time
from uuid import uuid4
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import digest
from test_manual_task_commands import prepared


def test_result_action_lost_before_owner_admission_explicit_retry_uses_same_id(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        original=remote.request;seen=[];rid=str(uuid4())
        def request(method,path,**kwargs):
            if method!='POST' or '/results/' not in path:return original(method,path,**kwargs)
            body=kwargs['json'];seen.append(dict(body))
            if len(seen)==1:raise CloudError('cloud_connection_failed',503)
            cid=body['command_id'];now=time.time()
            receipt=dict(command_id=cid,resource_id=binding['task_id'],operation='result:abandon',expected_generation=1,
                request_digest=digest(dict(task_id=binding['task_id'],result_id=rid,action='abandon',request=body)),
                status='succeeded',http_status=200,error_code=None,result=dict(result_id=rid,revision=3),created_at=now,updated_at=now)
            remote.commands[cid]=receipt;return receipt
        remote.request=request
        try:
            value=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],result_id=rid,expected_revision=2,action='abandon')
            op=await manager.commands.submit(domain,binding['binding_id'],'result_abandon',value);await manager.drain()
            assert domain.repo.operation(op['command_id'],sid)['phase']=='unknown'
            await manager.retry(domain,op['command_id'],sid);await manager.drain()
            assert domain.repo.operation(op['command_id'],sid)['phase']=='succeeded'
            assert seen==[dict(command_id=value['command_id'],expected_revision=2)]*2
            assert sum(c==value['command_id'] for c in remote.commands)==1
        finally:await manager.close();store.close()
    asyncio.run(scenario())
