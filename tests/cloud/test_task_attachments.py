"""Ready uploads do not authorize a Runner until a message owns the attachment."""
from uuid import uuid4
import pytest
from lifecycle_helpers import config,template
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CreateProject
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_runner.state import read_json
from naiyue_test_agent.contracts.cloud_inputs import InputPackage


def setup(runtime,tmp_path):
    source=tmp_path/'selected';source.mkdir();(source/'.env').write_text('chosen')
    captured=capture(source,{'schema':3,'paths':['.env']},tmp_path/'captured',runtime.settings.input_limits)
    package=InputPackage.model_validate(read_json(captured.directory/'input.json'))
    with runtime.database.transaction(write=True) as db:
        db.execute('INSERT INTO meta(key,value) VALUES(?,?)',('input:'+package.input_id,package.model_dump_json(by_alias=True)))
    project=runtime.commands.create_project(CreateProject(command_id=str(uuid4()),name='files'))
    request=dict(command_id=str(uuid4()),project_id=project.resource_id,template_id='python-tests')
    task=runtime.inputs.create_task(request).result['task']
    return task,package


def test_attachment_grant_is_idempotent_and_bound_to_task(tmp_path):
    from naiyue_test_agent.application.cloud.attachments import attach,for_command
    with open_cloud_runtime(config(tmp_path).model_copy(update={'templates':{'python-tests':template(file_transfer_version=1)}})) as r:
        task,package=setup(r,tmp_path);aid=str(uuid4())
        value=dict(attachment_id=aid,input_id=package.input_id,display_name='Selected files')
        result=attach(r,task['task_id'],value)
        assert attach(r,task['task_id'],value)==result
        assert result.input_id==package.input_id and result.size==package.size
        with pytest.raises(CloudError,match='attachment_identity_conflict'):
            attach(r,task['task_id'],value|{'display_name':'changed'})
        with pytest.raises(CloudError,match='command_not_found'):
            for_command(r,task['task_id'],str(uuid4()),aid)
        current=r.registry.get_task(task['task_id'])
        assert current.attachments[aid]==result and current.active_run_id is None


def test_attachment_registration_rejects_deleted_task_and_missing_package(tmp_path):
    from naiyue_test_agent.application.cloud.attachments import attach
    with open_cloud_runtime(config(tmp_path).model_copy(update={'templates':{'python-tests':template(file_transfer_version=1)}})) as r:
        task,package=setup(r,tmp_path)
        with pytest.raises(CloudError,match='input_not_found'):
            attach(r,task['task_id'],dict(attachment_id=str(uuid4()),input_id='f'*64,display_name='files'))
        current=r.registry.get_task(task['task_id']);deleted=current.model_copy(update={'lifecycle':'deleting'})
        r.registry.save_task_cas(deleted,expected_revision=current.revision,expected_generation=current.generation,revoked_at=0)
        with pytest.raises(CloudError,match='task_not_available'):
            attach(r,task['task_id'],dict(attachment_id=str(uuid4()),input_id=package.input_id,display_name='files'))


def test_task_attachment_download_requires_accepted_command_and_own_token(tmp_path):
    import asyncio,httpx,time
    from naiyue_test_agent.service.cloud.app import create_cloud_apps
    from naiyue_test_agent.application.cloud.attachments import attach
    from naiyue_test_agent.contracts.cloud_tasks import CommandReceipt
    from naiyue_test_agent.contracts.cloud_runner import RunCommand
    from cloud_helpers import owner_headers
    from test_input_import import transfer_request
    from naiyue_test_agent.contracts.cloud_inputs import CapturedInput
    async def scenario(r):
        task,package=setup(r,tmp_path);aid=str(uuid4());tid=task['task_id'];cid=str(uuid4())
        # Populate the real transfer store through its normal upload/commit boundary.
        with r.database.transaction(write=True) as db:db.execute('DELETE FROM meta WHERE key=?',('input:'+package.input_id,))
        captured=CapturedInput.model_validate_json((tmp_path/'captured'/'receipt.json').read_bytes())
        owner,internal=create_cloud_apps(r)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,headers=owner_headers(r)) as c:
            transfer=(await c.post('/api/cloud/v1/transfers',json=transfer_request(captured))).json()['result']['transfer']
            url='/api/cloud/v1/transfers/'+transfer['transfer_id']
            raw=(captured.directory/'archive').read_bytes()
            assert (await c.put(url+'/bytes',content=raw,headers={'Content-Type':'application/octet-stream','Upload-Offset':'0','X-Content-SHA256':captured.archive_sha256})).status_code==200
            assert (await c.post(url+'/commit',json={})).status_code==200
            response=await c.post('/api/cloud/v1/tasks/'+tid+'/attachments',json=dict(attachment_id=aid,input_id=package.input_id,display_name='files'))
            assert response.status_code==201,response.text
        attachment=attach(r,tid,dict(attachment_id=aid,input_id=package.input_id,display_name='files'))
        token=r.credentials.issue(tid,scopes=['inputs:read']).token.get_secret_value()
        url=f'/internal/cloud/v1/tasks/{tid}/commands/{cid}/attachments/{aid}'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal),base_url='http://'+r.settings.task_listen) as c:
            assert (await c.get(url)).status_code==401
            headers={'Authorization':'Bearer '+token}
            assert (await c.get(url,headers=headers)).status_code==404
            command=RunCommand(command_id=cid,run_id=uuid4().hex,message='use files',model_id='A',attachments=[attachment])
            receipt=CommandReceipt(command_id=cid,resource_id=tid,operation='run:start',request_digest='a'*64,status='running',http_status=202,
                expected_generation=1,result={'command':command.model_dump()},created_at=time.time(),updated_at=time.time())
            with r.database.transaction(write=True) as db:
                db.execute('INSERT INTO commands(command_id,request_digest,resource_id,record_json) VALUES(?,?,?,?)',
                    (cid,receipt.request_digest,tid,receipt.model_dump_json()))
            info=await c.get(url,headers=headers)
            assert info.status_code==200 and info.json()['input_id']==package.input_id
            downloaded=await c.get(url+'/archive',headers=headers|{'Range':'bytes=0-31'})
            assert downloaded.status_code==206 and downloaded.content==raw[:32]
            assert (await c.get(url.replace(aid,str(uuid4())),headers=headers)).status_code==404
            other=r.inputs.create_task(dict(command_id=str(uuid4()),project_id=task['project_id'],template_id='python-tests')).resource_id
            other_token=r.credentials.issue(other,scopes=['inputs:read']).token.get_secret_value()
            assert (await c.get(url,headers={'Authorization':'Bearer '+other_token})).status_code==401
    with open_cloud_runtime(config(tmp_path).model_copy(update={'templates':{'python-tests':template(file_transfer_version=1)}})) as r:
        asyncio.run(scenario(r))


def test_run_admission_freezes_only_owned_attachment_ids(tmp_path):
    from test_runner_control import prepared,client,connect,hello
    from test_results import run_body
    from cloud_helpers import owner_headers
    from naiyue_test_agent.contracts.cloud_tasks import TaskAttachment
    with open_cloud_runtime(config(tmp_path)) as r:
        bootstrap=prepared(r);task=r.registry.get_task(bootstrap.task_id);aid=str(uuid4())
        from test_input_download import uploaded
        source=tmp_path/'run-files';source.mkdir();(source/'.env').write_text('chosen')
        captured=capture(source,{'schema':3,'paths':['.env']},tmp_path/'run-package',r.settings.input_limits)
        transfer=uploaded(r,captured);package=r.inputs.commit(transfer.transfer_id)
        attachment=TaskAttachment(attachment_id=aid,input_id=package.input_id,display_name='files',size=package.size)
        pin=template(file_transfer_version=1)
        r.registry.save_task_cas(task.model_copy(update={'template_snapshot':pin,'template_digest':pin.fingerprint,'attachments':{aid:attachment}}),
            expected_revision=task.revision,expected_generation=task.generation,revoked_at=0)
        with client(r) as c,connect(c,r,bootstrap) as ws:
            ws.send_json(hello(bootstrap));ws.receive_json()
            task=r.registry.get_task(bootstrap.task_id);body=run_body(task)
            url='/api/cloud/v1/tasks/'+task.task_id+'/runs'
            denied=c.post(url,headers=owner_headers(r),json=body|{'attachment_ids':[str(uuid4())]})
            assert denied.status_code==404 and denied.json()['error']['code']=='attachment_not_found'
            assert r.registry.get_task(task.task_id).active_run_id is None
            sent=c.post(url,headers=owner_headers(r),json=body|{'attachment_ids':[aid]})
            assert sent.status_code==202,sent.text
            frame=ws.receive_json()
            assert frame['command']['attachments']==[attachment.model_dump()]
            assert sent.json()['result']['command']['attachments']==[attachment.model_dump()]
