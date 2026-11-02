import asyncio
import pytest
from test_manual_task_bindings import uid
from delete_integration_helpers import pair
from naiyue_test_agent.storage.errors import SessionPersistenceError
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('mode',['normal','lost_ack','offline'])
def test_real_owner_to_local_binding_deletion_and_parent_guard(tmp_path,monkeypatch,mode):
    async def run():
        async with pair(tmp_path,monkeypatch) as t:
            preview=await t.manager.commands.delete_preview(t.domain,t.bid,t.sid)
            request=dict(command_id=uid(),local_session_id=t.sid,expected_binding_revision=t.binding['revision'],
                expected_revision=preview['revision'],expected_generation=preview['generation'],
                preview_digest=preview['preview_digest'],acknowledge_unknown=False)
            t.state.drop=mode=='lost_ack';t.state.offline=mode=='offline'
            op=await t.manager.commands.submit(t.domain,t.bid,'delete',request);await t.manager.drain()
            if mode=='offline':
                assert t.domain.repo.binding(t.bid,t.sid)['retained']
                with pytest.raises(SessionPersistenceError,match='session_cloud_tasks_retained'):t.store.runs.delete(t.sid,())
                t.state.offline=False
                await t.manager.retry(t.domain,op['command_id'],t.sid);await t.manager.drain()
            if mode=='lost_ack':
                assert t.domain.repo.binding(t.bid,t.sid)['retained']
                assert t.runtime.registry.get_task(t.binding['task_id']).lifecycle=='deleted'
                op=await t.manager.commands.reconcile(t.domain,op['command_id'],t.sid)
            saved=t.domain.repo.operation(op['command_id'],t.sid)
            assert saved['phase']=='succeeded' and not t.domain.repo.binding(t.bid,t.sid)['retained']
            assert t.state.posts.count('/api/cloud/v1/tasks/'+t.binding['task_id']+'/delete')==1
            assert len([c for c in t.backend.calls if c[0]=='kill'])==1
            assert not t.runtime._histories
            create_id=t.binding['create_command_id']
            await t.manager.retry(t.domain,create_id,t.sid);await t.manager.drain()
            with pytest.raises(CloudError,match='cloud_task_unavailable'):
                await t.manager.cancel(t.domain,create_id,t.sid)
            assert t.domain.repo.list(t.sid)==[]
            t.store.runs.delete(t.sid,())
    asyncio.run(run())


def test_local_delete_routes_bind_parent_identity_and_do_not_touch_main_chat(tmp_path,monkeypatch):
    from test_manual_commands_routes import configured
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            seen=[]
            async def preview(*args):
                seen.append(args[1:]);return {'task_id':t.remote.task['task_id'],'preview_digest':'a'*64}
            monkeypatch.setattr(t.manager.commands,'delete_preview',preview)
            path=t.base+'/cloud-tasks/'+t.bid
            denied=await t.client.get(path+'/delete-preview',params=t.query,headers=t.headers|{'Authorization':''})
            assert denied.status_code==401 and not seen
            response=await t.client.get(path+'/delete-preview',params=t.query,headers=t.headers)
            assert response.status_code==200 and seen==[(t.bid,t.sid)]
            calls=[]
            async def submit(domain,bid,kind,request):
                calls.append((bid,kind,request));return {'status':'submitted'}
            monkeypatch.setattr(t.manager.commands,'submit',submit)
            value=dict(command_id=uid(),local_session_id=t.sid,expected_binding_revision=t.binding['revision'],
                expected_revision=1,expected_generation=1,preview_digest='a'*64)
            assert (await t.client.post(path+'/delete',params=t.query,headers=t.headers,json=value)).status_code==202
            assert calls[0][0:2]==(t.bid,'delete')
            assert (await t.client.post(path+'/delete',params=t.query,headers=t.headers,json=value|{'directory':'/tmp'})).status_code==422
            assert t.model.seen==[] and (await t.host.sessions.status(t.sid))['run_id'] is None
    asyncio.run(run())


def test_remote_owner_delete_is_adopted_from_original_receipt(tmp_path,monkeypatch):
    from test_task_delete import request
    async def run():
        async with pair(tmp_path,monkeypatch) as t:
            tid=t.binding['task_id'];value=request(t.runtime,tid)
            assert (await t.runtime.deletions.command(tid,value)).status=='succeeded'
            assert t.domain.repo.binding(t.bid,t.sid)['retained']
            binding=await t.manager.read(t.domain,t.bid,t.sid)
            assert not binding['retained'] and binding['deletion']['delete_command_id']==value['command_id']
            assert t.domain.repo.operation(value['command_id'],t.sid)['phase']=='succeeded'
            t.store.runs.delete(t.sid,())
    asyncio.run(run())


def test_parent_can_be_deleted_immediately_after_receipt_without_late_refresh(tmp_path,monkeypatch):
    async def run():
        async with pair(tmp_path,monkeypatch) as t:
            preview=await t.manager.commands.delete_preview(t.domain,t.bid,t.sid)
            original=t.domain.repo.commands.record
            def record(*args):
                op=original(*args)
                if op['kind']=='delete' and op['phase']=='succeeded':t.store.runs.delete(t.sid,())
                return op
            monkeypatch.setattr(t.domain.repo.commands,'record',record)
            await t.manager.commands.submit(t.domain,t.bid,'delete',dict(command_id=uid(),local_session_id=t.sid,
                expected_binding_revision=t.binding['revision'],expected_revision=preview['revision'],
                expected_generation=preview['generation'],preview_digest=preview['preview_digest'],acknowledge_unknown=False))
            await t.manager.drain()
            assert t.store.database.connection.execute('SELECT COUNT(*) FROM naiyue_cloud_bindings').fetchone()[0]==0
            assert not t.manager.commands.flights
    asyncio.run(run())
