"""Local uploads are durable per task and never start a run or alter its files."""
import asyncio
from uuid import uuid4
from test_manual_task_preparation import setup
from naiyue_test_agent.contracts.cloud_tasks import CloudError
import pytest


def test_upload_during_run_is_ready_only_and_duplicate_does_not_reupload(tmp_path):
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path)
        try:
            created=await manager.submit(domain,'project',dict(command_id=str(uuid4()),local_session_id=sid,profile_id='work',template_id='test'))
            await manager.drain();bid=created['binding']['binding_id']
            remote.task['active_run_id']='a'*32
            original=remote.request;grants=[]
            def request(method,path,**kwargs):
                if path.endswith('/attachments'):
                    grants.append(kwargs['json']);value=kwargs['json']
                    package=next((domain.cache/'inputs').glob(value['input_id']+'/receipt.json'),None)
                    assert package is not None
                    import json
                    return value|{'size':json.loads(package.read_text())['size']}
                return original(method,path,**kwargs)
            remote.request=request
            original_upload=remote.upload
            def upload(path,*,upload_id,check_cancel):
                assert upload_id==body['attachment_id'];check_cancel();return original_upload(path)
            remote.upload=upload
            (tmp_path/'source'/'.env').write_text('chosen')
            body=dict(attachment_id=str(uuid4()),paths=['.env'],display_name='files')
            pending=await manager.attachments.submit(domain,bid,sid,tmp_path/'source',body)
            assert pending['phase']=='uploading'
            await manager.drain()
            rows=await manager.attachments.list(domain,bid,sid)
            assert len(rows)==1 and rows[0]['phase']=='ready' and rows[0]['input_id']==grants[0]['input_id']
            assert remote.task['active_run_id']=='a'*32 and not any('/runs' in path for path,_ in remote.posts)
            assert await manager.attachments.submit(domain,bid,sid,tmp_path/'source',body)==rows[0]
            await manager.drain();assert len(remote.uploads)==1
            with pytest.raises(CloudError,match='attachment_identity_conflict'):
                await manager.attachments.submit(domain,bid,sid,tmp_path/'source',body|{'paths':['another']})
        finally:await manager.close();store.close()
    asyncio.run(scenario())


def test_draft_and_message_attachment_ownership_are_checked_atomically(tmp_path):
    from test_manual_task_commands import prepared
    from naiyue_test_agent.storage.cloud_attachments import AttachmentRepository
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            bid=binding['binding_id'];aid=str(uuid4());repo=AttachmentRepository(domain.repo.database)
            repo.reserve(bid,sid,tmp_path/'source',dict(attachment_id=aid,paths=['file.txt'],display_name='file'))
            draft=domain.repo.commands.save_draft(bid,sid,dict(expected_draft_revision=0,text='use file',model_id='A',attachment_ids=[aid]))
            assert draft['attachment_ids']==[aid]
            request=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],
                text='use file',model_id='A',draft_revision=1,attachment_ids=[aid])
            with pytest.raises(CloudError,match='attachment_not_ready'):domain.repo.commands.reserve(bid,sid,'message',request)
            repo.update(bid,sid,aid,phase='ready',input_id='c'*64,size=12)
            operation=domain.repo.commands.reserve(bid,sid,'message',request)
            assert operation['request']['attachment_ids']==[aid]
            assert repo.read(bid,sid,aid)['phase']=='attached'
            with pytest.raises(CloudError,match='attachment_already_sent'):repo.update(bid,sid,aid,phase='cancelled')
        finally:await manager.close();store.close()
    asyncio.run(scenario())


def test_interrupted_upload_has_explicit_retry_state_after_service_restart(tmp_path):
    from test_manual_task_commands import prepared
    from naiyue_test_agent.storage.cloud_attachments import AttachmentRepository
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            aid=str(uuid4());repo=AttachmentRepository(domain.repo.database)
            repo.reserve(binding['binding_id'],sid,tmp_path,dict(attachment_id=aid,display_name='files',paths=['report.txt']))
            rows=await manager.attachments.list(domain,binding['binding_id'],sid)
            assert rows[0]['phase']=='failed' and rows[0]['error_code']=='attachment_upload_interrupted'
            assert manager.attachments.flights=={}
        finally:await manager.close();store.close()
    asyncio.run(scenario())
