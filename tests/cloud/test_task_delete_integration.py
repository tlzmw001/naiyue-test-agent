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
            summary=await t.manager.commands.reconcile(t.domain,op['command_id'],t.sid)
            assert summary['phase']=='succeeded'
            assert summary['steps']['primary']['receipt']['result']['input_cleanup']['state']=='completed'
            retried=await t.manager.commands.reconcile(t.domain,op['command_id'],t.sid,cleanup_retry=True)
            assert retried['phase']=='succeeded'
            assert t.state.posts.count('/api/cloud/v1/commands/'+op['command_id']+'/reconcile')==1
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


@pytest.mark.parametrize('release_interrupted',[False,True])
def test_full_delete_settles_original_pending_attachment_use(tmp_path,monkeypatch,release_interrupted):
    from naiyue_test_agent.agent_runtime.async_utils import run_blocking
    from naiyue_test_agent.cloud_files.client import CloudClient
    from naiyue_test_agent.contracts.cloud_local_tasks import command_summary
    from naiyue_test_agent.storage.cloud_attachments import AttachmentRepository

    async def run():
        async with pair(tmp_path,monkeypatch) as t:
            aid=uid();ident=t.binding['input_id']
            repo=AttachmentRepository(t.domain.repo.database)
            repo.reserve(t.bid,t.sid,tmp_path,dict(attachment_id=aid,display_name='report',paths=['report.txt']))
            repo.update(t.bid,t.sid,aid,phase='failed',input_id=ident)
            async with t.manager.connection(t.binding) as client:
                use=await run_blocking(client.acquire_input_use,ident,aid)
            assert t.runtime.inputs.uses.get(use)['state']=='held'
            release=CloudClient.release_operation_input
            attempts=[]
            def interrupted(client,ident,operation_id):
                attempts.append(operation_id)
                if release_interrupted and len(attempts)==1:raise CloudError('cloud_connection_failed',503)
                return release(client,ident,operation_id)
            monkeypatch.setattr(CloudClient,'release_operation_input',interrupted)
            preview=await t.manager.commands.delete_preview(t.domain,t.bid,t.sid)
            request=dict(command_id=uid(),local_session_id=t.sid,expected_binding_revision=t.binding['revision'],
                expected_revision=preview['revision'],expected_generation=preview['generation'],
                preview_digest=preview['preview_digest'],acknowledge_unknown=False)
            await t.manager.commands.submit(t.domain,t.bid,'delete',request);await t.manager.drain()
            cid=request['command_id']
            saved=t.domain.repo.operation(cid,t.sid)
            assert saved['phase']=='succeeded'
            if release_interrupted:
                assert command_summary(saved)['attachment_release_pending'] is True
                assert t.runtime.inputs.uses.get(use)['state']=='held'
                with pytest.raises(SessionPersistenceError,match='session_cloud_attachment_release_pending'):
                    t.store.runs.delete(t.sid,())
                await t.manager.commands.reconcile(t.domain,cid,t.sid,cleanup_retry=True)
                saved=t.domain.repo.operation(cid,t.sid)
            assert t.runtime.inputs.uses.get(use)['state']=='released'
            assert not command_summary(saved).get('attachment_release_pending')
            assert saved['steps']['primary']['receipt']['result']['input_cleanup']['candidates'][0]['state']=='reclaimed'
            assert attempts==([aid,aid] if release_interrupted else [aid])
            t.store.runs.delete(t.sid,())
    asyncio.run(run())
