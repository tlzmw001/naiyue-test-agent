import asyncio
import threading
import pytest
from test_manual_task_commands import prepared
from test_manual_task_bindings import uid
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('missing_profile',[False,True])
def test_forget_removes_binding_without_remote_deletion_and_cannot_replay(tmp_path,missing_profile):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            bid=binding['binding_id']
            old=domain.repo.commands.reserve(bid,sid,'message',dict(command_id=uid(),local_session_id=sid,
                expected_binding_revision=binding['revision'],text='private prompt',model_id='A'))
            if missing_profile:manager.book.profiles.clear()
            async def unavailable(*args):
                raise AssertionError('forget must not open a remote connection')
            manager.connections.get=unavailable
            value=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],
                acknowledge_unmanaged=True)
            removed=await manager.forget(domain,bid,value)
            assert removed['retained'] is False and removed['removal']['command_id']==value['command_id']
            assert not removed.get('deletion') and domain.repo.list(sid)==[]
            assert await manager.forget(domain,bid,value)==removed
            assert await manager.read(domain,bid,sid)==removed
            with pytest.raises(CloudError,match='cloud_task_unavailable'):
                async with manager.connection(removed):
                    raise AssertionError('removed binding must not connect')
            assert domain.repo.cache_task(bid,sid,binding['cloud'])==removed
            retired=domain.repo.operation(old['command_id'],sid)
            assert retired['error_code']=='cloud_task_removed' and retired['steps']=={}
            assert manager.commands.launch(domain,retired,explicit=True)==retired
            assert domain.repo.commands.fail(old['command_id'],sid,'unknown','late_error')==retired
            assert domain.repo.commands.record(old['command_id'],sid,'primary',{})==retired
            with pytest.raises(CloudError,match='cloud_task_unavailable'):
                domain.repo.commands.dispatch(old['command_id'],sid,'primary')
            create=domain.repo.operation(binding['create_command_id'],sid)
            assert create['phase']=='cancelled' and create['cancel_requested']
            assert 'private prompt' not in str(retired)
            assert store.sessions.get(sid)['project_id']==domain.project_id
            store.runs.delete(sid,())
        finally:
            await manager.close()
            store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('kind',['message','attachment'])
@pytest.mark.parametrize('cancel_waiter',[False,True])
def test_retry_waits_for_forget_admission_before_starting_remote_work(tmp_path,monkeypatch,kind,cancel_waiter):
    from test_manual_task_commands import CommandRemote
    from naiyue_test_agent.storage.cloud_attachments import AttachmentRepository

    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        waiting=asyncio.Event()
        class Admission(asyncio.Lock):
            async def acquire(self):
                if self.locked():waiting.set()
                return await super().acquire()
        manager.admission=Admission()
        entered,release=threading.Event(),threading.Event()
        network_entered,network_release=threading.Event(),threading.Event()
        original_forget=domain.repo.commands.forget
        def forget(*args):
            entered.set()
            assert release.wait(10)
            return original_forget(*args)
        monkeypatch.setattr(domain.repo.commands,'forget',forget)
        bid=binding['binding_id'];cid=uid()
        if kind=='message':
            class Remote(CommandRemote):
                def request(self,method,path,**kwargs):
                    if method=='POST' and path.endswith('/runs'):
                        network_entered.set()
                        assert network_release.wait(10)
                    return super().request(method,path,**kwargs)
            adapter=Remote(remote);adapter.settings=remote.settings
            manager.connections.client=adapter
            domain.repo.commands.reserve(bid,sid,'message',dict(command_id=cid,local_session_id=sid,
                expected_binding_revision=binding['revision'],text='work',model_id='A',draft_revision=None))
            async def retry():return await manager.retry(domain,cid,sid)
        else:
            original_upload=remote.upload
            def upload(path,**kwargs):
                kwargs['check_cancel']()
                network_entered.set()
                assert network_release.wait(10)
                return original_upload(path)
            monkeypatch.setattr(remote,'upload',upload)
            source_handle=domain.repo.operation(binding['create_command_id'],sid)['request']['source_handle']
            repo=AttachmentRepository(domain.repo.database)
            repo.reserve(bid,sid,tmp_path/'source',dict(attachment_id=cid,paths=['test.py'],display_name='file'))
            repo.update(bid,sid,cid,phase='failed',source_handle=source_handle,input_id=binding['input_id'])
            async def retry():return await manager.attachments.action(domain,bid,sid,cid,'retry')
        uploads=len(remote.uploads)
        removal=asyncio.create_task(manager.forget(domain,bid,dict(command_id=uid(),local_session_id=sid,
            expected_binding_revision=binding['revision'],acknowledge_unmanaged=True)))
        retry_task=None;watchers=[];errors=[]
        try:
            assert await asyncio.to_thread(entered.wait,10)
            retry_task=asyncio.create_task(retry())
            watchers=[asyncio.create_task(waiting.wait()),asyncio.create_task(asyncio.to_thread(network_entered.wait,10))]
            await asyncio.wait_for(asyncio.wait(watchers,return_when=asyncio.FIRST_COMPLETED),10)
            assert waiting.is_set(), 'retry bypassed the held removal admission lock'
            assert not network_entered.is_set()
            if cancel_waiter:retry_task.cancel()
            release.set()
            assert not (await removal)['retained']
            if cancel_waiter:
                with pytest.raises(asyncio.CancelledError):
                    await retry_task
            elif kind=='message':
                assert (await retry_task)['error_code']=='cloud_task_removed'
            else:
                with pytest.raises(CloudError,match='attachment_not_found'):
                    await retry_task
            assert not network_entered.is_set()
            assert not remote.task['active_run_id'] and len(remote.uploads)==uploads
        finally:
            release.set();network_release.set();network_entered.set();waiting.set()
            await asyncio.gather(removal,*([retry_task] if retry_task else []),*watchers,return_exceptions=True)
            errors=await asyncio.gather(manager.drain(),return_exceptions=True)
            await manager.close();store.close()
        assert errors==[None]
    asyncio.run(scenario())


def test_forget_http_auth_confirmation_and_session_are_required(tmp_path,monkeypatch):
    from test_manual_commands_routes import configured
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            t.manager.book.profiles.clear()
            calls=list(t.adapter.calls)
            url=t.base+'/cloud-tasks/'+t.bid+'/forget'
            value=dict(command_id=uid(),local_session_id=t.sid,expected_binding_revision=t.binding['revision'],
                acknowledge_unmanaged=True)
            denied=await t.client.post(url,params=t.query,headers=t.headers|{'Authorization':''},json=value)
            assert denied.status_code==401
            invalid=await t.client.post(url,params=t.query,headers=t.headers,json=value|dict(acknowledge_unmanaged=False))
            assert invalid.status_code==422
            other=await t.host.sessions.create('A')
            cross=await t.client.post(url,params=t.query,headers=t.headers,json=value|dict(local_session_id=other))
            assert cross.status_code==404
            success=await t.client.post(url,params=t.query,headers=t.headers,json=value)
            assert success.status_code==200 and success.json()['retained'] is False
            assert success.json()['removal']['command_id']==value['command_id']
            repeated=await t.client.post(url,params=t.query,headers=t.headers,json=value)
            assert repeated.json()==success.json()
            listed=await t.client.get(t.base+'/cloud-tasks',params=t.query,headers=t.headers)
            assert listed.json()==[]
            assert t.adapter.calls==calls and t.model.seen==[]
            assert (await t.host.sessions.status(t.sid))['run_id'] is None
    asyncio.run(scenario())


def test_forget_checks_session_and_revision_before_changing_anything(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            value=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision']+1,
                acknowledge_unmanaged=True)
            with pytest.raises(CloudError,match='cloud_binding_conflict'):
                await manager.forget(domain,binding['binding_id'],value)
            with pytest.raises(CloudError,match='cloud_binding_not_found'):
                await manager.forget(domain,binding['binding_id'],value|dict(local_session_id=uid()))
            assert domain.repo.binding(binding['binding_id'],sid)['retained']
        finally:
            await manager.close()
            store.close()
    asyncio.run(scenario())


def test_forget_rejects_active_local_producer_and_succeeds_once_it_finishes(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        key=(domain.project_id,uid())
        try:
            value=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],
                acknowledge_unmanaged=True)
            manager.slots.add(key)
            with pytest.raises(CloudError,match='cloud_local_work_active'):
                await manager.forget(domain,binding['binding_id'],value)
            assert domain.repo.binding(binding['binding_id'],sid)['retained']
            manager.slots.remove(key)
            assert not (await manager.forget(domain,binding['binding_id'],value))['retained']
        finally:
            manager.slots.discard(key)
            await manager.close()
            store.close()
    asyncio.run(scenario())
