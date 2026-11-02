"""Authenticated immutable artifact transfer, publication and owner completion."""
import hashlib
from uuid import uuid4

import pytest

from cloud_helpers import owner_headers
from lifecycle_helpers import config
from test_runner_control import prepared,client,connect,hello
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime


def accept(r,c,ws,b,path='reports/report.bin'):
    value=hello(b);value['capabilities']=['artifacts:capture'];ws.send_json(value)
    welcome=ws.receive_json();fence={k:v for k,v in welcome.items() if k!='kind'}
    task=r.registry.get_task(b.task_id)
    request=dict(command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,relative_name=path)
    route='/api/cloud/v1/tasks/'+b.task_id+'/artifacts'
    response=c.post(route,headers=owner_headers(r),json=request)
    assert response.status_code==202,response.text
    receipt=response.json();frame=ws.receive_json();assert frame['kind']=='artifact_capture'
    assert c.post(route,headers=owner_headers(r),json=request).json()==receipt
    return frame['command'],fence


def metadata(b,command,data):
    return dict(**command,task_id=b.task_id,project_id=b.project_id,input_id=b.input_id,generation=b.generation,
        size=len(data),sha256=hashlib.sha256(data).hexdigest(),captured_at=1.0)


def task_headers(r,b):
    return {'Host':r.settings.task_listen,'Authorization':'Bearer '+b.token.get_secret_value()}


def prepare(c,r,b,record):
    path='/internal/cloud/v1/tasks/'+b.task_id+'/artifacts/'+record['artifact_id']
    response=c.post(path+'/prepare',headers=task_headers(r,b),json=record)
    assert response.status_code==200,response.text
    return path


def upload(c,r,b,path,record,data):
    return c.put(path+'/bytes',headers=task_headers(r,b)|{'Content-Type':'application/octet-stream',
        'Content-Length':str(record['size']),'X-Content-SHA256':record['sha256'],'Upload-Offset':'0'},content=data)


@pytest.mark.parametrize('data',[b'',b'\x00\xff\r\n'*1024],ids=['empty','binary'])
def test_available_only_after_commit_completion_only_after_runner_ack_and_reopen(tmp_path,data):
    settings=config(tmp_path)
    with open_cloud_runtime(settings) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            command,fence=accept(r,c,ws,b);record=metadata(b,command,data)
            owner='/api/cloud/v1/tasks/'+b.task_id+'/artifacts/'+record['artifact_id']
            assert c.get(owner,headers=owner_headers(r)).status_code==404
            path=prepare(c,r,b,record)
            assert upload(c,r,b,path,record,data).status_code==200
            assert c.get(owner+'/bytes',headers=owner_headers(r)).status_code==404
            assert c.post(path+'/commit',headers=task_headers(r,b),json={}).status_code==200
            assert r.registry.get_command(command['command_id']).status=='running'
            assert r.registry.get_task(b.task_id).artifact_operation_id==command['command_id']
            response=c.get(owner+'/bytes',headers=owner_headers(r))
            assert response.content==data and response.headers['etag']=='"'+record['sha256']+'"'
            assert c.get(owner+'/bytes').status_code==401
            ws.send_json(dict(kind='artifact_finished',command_id=command['command_id'],artifact_id=record['artifact_id'],
                status='available',quiescent=True,phase='ready',**fence))
            ws.send_json(dict(kind='heartbeat',**fence));assert ws.receive_json()['kind']=='welcome'
            assert r.registry.get_command(command['command_id']).status=='succeeded'
            assert r.registry.get_task(b.task_id).artifact_operation_id is None
    with open_cloud_runtime(settings) as r,client(r) as c:
        assert c.get(owner+'/bytes',headers=owner_headers(r)).content==data


@pytest.mark.parametrize('bad',['short','hash','generation','no_scope'])
def test_artifact_bad_transfer_never_becomes_available(tmp_path,bad):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            command,_=accept(r,c,ws,b);data=b'original';record=metadata(b,command,data)
            if bad=='generation':
                record['generation']+=1
                response=c.post('/internal/cloud/v1/tasks/'+b.task_id+'/artifacts/'+record['artifact_id']+'/prepare',
                    headers=task_headers(r,b),json=record)
                assert response.status_code==409
            else:
                path=prepare(c,r,b,record)
                if bad=='no_scope':
                    issued=r.credentials.issue(b.task_id,scopes=('events:append',))
                    response=c.put(path+'/bytes',headers=task_headers(r,b)|{'Authorization':'Bearer '+issued.token.get_secret_value()},content=data)
                    assert response.status_code==401
                else:
                    response=upload(c,r,b,path,record,data[:-1] if bad=='short' else b'bad-hash')
                    assert response.status_code==409
                assert c.post(path+'/commit',headers=task_headers(r,b),json={}).status_code==409
            assert c.get('/api/cloud/v1/tasks/'+b.task_id+'/artifacts/'+record['artifact_id'],headers=owner_headers(r)).status_code==404


def test_commit_published_before_index_failure_recovers_exact_bytes(tmp_path,monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            command,_=accept(r,c,ws,b);data=b'durable final bytes';record=metadata(b,command,data)
            path=prepare(c,r,b,record)
            assert upload(c,r,b,path,record,data).status_code==200
            original=r.artifacts.repository.available;calls=[]
            def fail_once(*args):
                calls.append(True)
                if len(calls)==1:raise OSError('controlled index failure after rename')
                return original(*args)
            monkeypatch.setattr(r.artifacts.repository,'available',fail_once)
            assert c.post(path+'/commit',headers=task_headers(r,b),json={}).status_code==503
            owner='/api/cloud/v1/tasks/'+b.task_id+'/artifacts/'+record['artifact_id']
            assert c.get(owner,headers=owner_headers(r)).status_code==404
            assert r.registry.get_task(b.task_id).artifact_operation_id==command['command_id']
            assert c.post(path+'/commit',headers=task_headers(r,b),json={}).status_code==200
            assert c.get(owner+'/bytes',headers=owner_headers(r)).content==data
            stored=r.artifacts.repository.get(b.task_id,record['artifact_id'])
            published=r.artifacts.store.directory('artifacts',b.task_id,record['artifact_id'])/'bytes'
            published.write_bytes(b'tampered service bytes')
            assert c.get(owner+'/bytes',headers=owner_headers(r)).status_code==409
            assert stored.state=='available' # Never return corrupted bytes as success.


def test_accepted_bounded_upload_survives_token_rotation_but_new_commit_requires_new_token(tmp_path):
    import asyncio
    from naiyue_test_agent.application.cloud.work import cloud_work
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            command,_=accept(r,c,ws,b);data=b'accepted immutable body';record=metadata(b,command,data)
            path=prepare(c,r,b,record);new=[]
            from starlette.datastructures import Headers
            class Request:
                headers=Headers({'content-type':'application/octet-stream','content-length':str(len(data)),
                    'x-content-sha256':record['sha256'],'upload-offset':'0'})
                scope={'headers':[(k.encode(),v.encode()) for k,v in headers.items()]}
                async def stream(self):
                    yield data[:3]
                    def rotate():
                        previous=r.credentials.repository.find(__import__('hashlib').sha256(b.token.get_secret_value().encode()).hexdigest())
                        r.credentials.revoke(b.task_id,previous.credential_id)
                        new.append(r.credentials.issue(b.task_id,scopes=('artifacts:write',)))
                    await cloud_work(r,rotate)
                    yield data[3:]
            async def scenario():
                transfer=await r.artifacts.upload(b.token.get_secret_value(),b.task_id,record['artifact_id'],Request())
                assert transfer.state=='uploaded'
                with pytest.raises(CloudError,match='unauthorized'):
                    await r.artifacts.commit_owned(b.token.get_secret_value(),b.task_id,record['artifact_id'])
                value=await r.artifacts.commit_owned(new[0].token.get_secret_value(),b.task_id,record['artifact_id'])
                assert value.sha256==record['sha256']
            asyncio.run(scenario())
