import asyncio
from uuid import uuid4
from types import SimpleNamespace
import pytest
from lifecycle_helpers import Backend, command, config, register
from test_task_delete import request
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.contracts.cloud_lifecycle import BackendFailure
from naiyue_test_agent.contracts.cloud_tasks import CloudError, CommandReceipt


def test_unknown_rpc_completed_locally_does_not_prove_remote_create_finished(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            backend=Backend();release=asyncio.Event();old=backend.create;pending=[]
            async def delayed(*args):
                async def remote():await release.wait();return await old(*args)
                pending.append(asyncio.create_task(remote()))
                raise BackendFailure('backend_unavailable')
            backend.create=delayed
            async with open_cloud_backend(r,backend=backend):
                task=register(r)
                assert (await r.lifecycle.command(task.task_id,command(task))).status=='unknown'
                value=request(r,task.task_id,acknowledge_unknown=True)
                receipt=await r.deletions.command(task.task_id,value)
                assert receipt.status=='unknown' and receipt.error_code=='sandbox_create_unconfirmed'
                assert (r.settings.data_dir/'tasks'/task.task_id).exists()
                assert r.resources.capacity()['counts']['retained']==1
                release.set();await asyncio.gather(*pending)
                assert backend.sandboxes
                receipt=await r.deletions.reconcile(value['command_id'])
                assert receipt.status=='succeeded' and not backend.sandboxes
        asyncio.run(run())


def test_discovered_id_is_durable_before_lost_kill_ack(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            backend=Backend();backend.lose_create_ack=True;backend.lose_stop_ack=True
            async with open_cloud_backend(r,backend=backend):
                task=register(r);await r.lifecycle.command(task.task_id,command(task))
                value=request(r,task.task_id,acknowledge_unknown=True)
                receipt=await r.deletions.command(task.task_id,value)
                assert receipt.status=='unknown' and receipt.result['sandbox_id']
                assert receipt.result['creation_unconfirmed'] is False and not backend.sandboxes
                assert (await r.deletions.reconcile(value['command_id'])).status=='succeeded'
                assert len([c for c in backend.calls if c[0]=='kill'])==1
        asyncio.run(run())


def test_old_receipt_text_is_redacted_and_full_normal_receipts_allow_bounded_delete(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r);cid=str(uuid4())
                old=CommandReceipt(command_id=cid,resource_id=task.task_id,operation='run:start',request_digest='a'*64,
                    status='failed',http_status=200,result={'command':{'message':'private task body'}},created_at=1.0,updated_at=1.0)
                with r.database.transaction(write=True) as c:
                    c.execute('INSERT INTO commands VALUES(?,?,?,?)',(cid,old.request_digest,old.resource_id,old.model_dump_json()))
                    count=c.execute('SELECT COUNT(*) FROM commands').fetchone()[0]
                r.settings=r.settings.model_copy(update={'max_receipts':count})
                assert (await r.deletions.command(task.task_id,request(r,task.task_id))).status=='succeeded'
                after=r.registry.get_command(cid)
                assert after.request_digest==old.request_digest and after.http_status==410
                assert after.error_code=='task_deleted' and 'private task body' not in after.model_dump_json()
                assert r.registry.get_task(task.task_id).title==''
        asyncio.run(run())


def test_deleted_snapshot_cannot_register_late_subscriber(tmp_path,monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r);entered=asyncio.Event();release=asyncio.Event();original=r.observations.snapshot
                async def paused(*args,**kwargs):
                    value=await original(*args,**kwargs);entered.set();await release.wait();return value
                monkeypatch.setattr(r.observations,'snapshot',paused)
                subscribing=asyncio.create_task(r.observations.subscribe(task.task_id))
                await asyncio.wait_for(entered.wait(),5)
                result=await r.deletions.command(task.task_id,request(r,task.task_id))
                assert result.status=='succeeded'
                release.set()
                with pytest.raises(CloudError,match='task_not_available'):await subscribing
                assert not r.observations.leases and task.task_id not in r.observations.states
        asyncio.run(run())


def test_existing_sse_ends_with_deleting_and_closed_socket_is_observation_only(tmp_path,caplog):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r);lease=await r.observations.subscribe(task.task_id)
                assert (await lease.next())['event']=='snapshot'
                async def closed(**kwargs):raise RuntimeError('already closed')
                r.control.connections[task.task_id]=SimpleNamespace(websocket=SimpleNamespace(close=closed))
                result=await r.deletions.command(task.task_id,request(r,task.task_id))
                assert result.status=='succeeded'
                assert (await lease.next())['event']=='task_deleting'
                with pytest.raises(StopAsyncIteration):await lease.next()
        asyncio.run(run())
    assert 'cloud_deleted_connection_close_failed' in caplog.text


def test_bootstrap_and_replace_cannot_recreate_deleted_storage(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                task=register(r)
                assert (await r.deletions.command(task.task_id,request(r,task.task_id))).status=='succeeded'
                with pytest.raises(CloudError,match='task_not_available'):r.control.prepare(task.task_id)
                current=r.registry.get_task(task.task_id)
                with pytest.raises(CloudError,match='task_not_available'):
                    r.replace_task(current.model_copy(update={'revision':current.revision+1,'lifecycle':'ready'}),
                        expected_revision=current.revision,expected_generation=current.generation)
                assert not (r.settings.data_dir/'tasks'/task.task_id).exists()
        asyncio.run(run())
