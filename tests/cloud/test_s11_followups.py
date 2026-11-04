"""Original-machine acceptance regressions, with deterministic local boundaries."""
import asyncio
from uuid import uuid4
import pytest
from test_manual_task_commands import prepared
from test_runner_control import prepared as runner_prepared, client, connect, hello
from lifecycle_helpers import config
from cloud_helpers import owner_headers
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime


def test_ready_snapshot_repairs_failed_binding_without_rewriting_create_receipt(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            cid=binding['create_command_id'];bid=binding['binding_id']
            domain.repo.update(cid,sid,phase='failed',error_code='environment_capacity')
            before=domain.repo.operation(cid,sid)
            current=await manager.read(domain,bid,sid)
            assert current['preparation']=='prepared'
            assert domain.repo.operation(cid,sid)==before
            assert (await manager.read(domain,bid,sid))['revision']==current['revision']
        finally:await manager.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('existing',['','Explicit title'])
def test_first_run_supplies_missing_title_atomically_and_preserves_existing(tmp_path,existing):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=runner_prepared(r)
        task=r.registry.get_task(b.task_id)
        r.replace_task(task.model_copy(update={'title':existing,'revision':task.revision+1}),expected_revision=task.revision,expected_generation=task.generation)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json()
            task=r.registry.get_task(b.task_id)
            body=dict(command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='  检查接口\n  并生成报告  ',model_id='A')
            path='/api/cloud/v1/tasks/'+b.task_id+'/runs'
            response=c.post(path,headers=owner_headers(r),json=body)
            assert response.status_code<300,response.text
            ws.receive_json()
            assert r.registry.get_task(b.task_id).title==(existing or '检查接口 并生成报告')
            revision=r.registry.get_task(b.task_id).revision
            assert c.post(path,headers=owner_headers(r),json=body).status_code<300
            assert r.registry.get_task(b.task_id).revision==revision


def test_operation_list_keeps_durable_acceptance_when_remote_observation_disconnects(tmp_path,caplog):
    from test_manual_task_commands import CommandRemote
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
            bid=binding['binding_id'];cid=str(uuid4())
            await manager.commands.submit(domain,bid,'message',dict(command_id=cid,local_session_id=sid,
                expected_binding_revision=binding['revision'],text='send once',model_id='A',draft_revision=None))
            await manager.drain()
            assert domain.repo.operation(cid,sid)['phase']=='accepted'
            latest=domain.repo.binding(bid,sid)
            domain.repo.commands.reserve(bid,sid,'message',dict(command_id=str(uuid4()),local_session_id=sid,
                expected_binding_revision=latest['revision'],text='unsent',model_id='A',draft_revision=None))
            reads=[]
            def disconnected(*args,**kwargs):
                reads.append(args);raise CloudError('cloud_connection_failed',502)
            adapter.request=disconnected
            values=await manager.commands.list(domain,bid,sid)
            assert next(v for v in values if v['command_id']==cid)['phase']=='accepted'
            assert len(adapter.calls)==1
            assert len(reads)==1
            assert 'cloud_command_observation_failed' in caplog.text
        finally:await manager.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('phase',['blocked','disconnected'])
def test_unready_snapshot_does_not_repair_failed_preparation(tmp_path,phase):
    async def run():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            domain.repo.update(binding['create_command_id'],sid,phase='failed',error_code='environment_capacity')
            remote.task.update(runner_phase=phase,revision=remote.task['revision']+1)
            current=await manager.read(domain,binding['binding_id'],sid)
            assert current['preparation']=='failed'
        finally:await manager.close();store.close()
    asyncio.run(run())
