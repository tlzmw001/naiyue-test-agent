"""Real run boundary, frozen slots and owner queries share one durable result."""
from uuid import uuid4
import pytest
from lifecycle_helpers import config
from cloud_helpers import owner_headers
from test_runner_control import prepared,client,connect,hello
from test_results import finish,run_body
from test_artifact_api import prepare,upload,task_headers
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result,verify_spool
from pathlib import Path


def frozen(r,b,c,ws,tmp_path):
    ws.send_json(hello(b));ws.receive_json()
    command,_=finish(r,b,c,ws,run_body(r.registry.get_task(b.task_id)))
    rid=command['result_plan']['result_id'];raw=b.token.get_secret_value();record=r.results.begin(raw,b.task_id,rid)
    root=tmp_path/'work';root.mkdir();(root/'new.py').write_text('new code')
    reports=root/record.plan.output_directory/record.plan.source_run_id;reports.mkdir(parents=True)
    (reports/'changes.patch').write_text('report name may match internal patch')
    spool=tmp_path/'spool';freeze_result(root,Path(b.input_directory),spool,record.plan,record.boundary,record.generation,r.settings.result_limits,r.settings.input_limits)
    state=verify_spool(spool,record.plan,record.boundary,r.settings.result_limits)
    return rid,raw,spool,state


def transfer(r,b,c,spool,state):
    for file in state.bundle.files:
        a=file.artifact;record=a.model_dump(mode='json');path=prepare(c,r,b,record)
        assert upload(c,r,b,path,record,(spool/'files'/a.artifact_id).read_bytes()).status_code==200
        assert c.post(path+'/commit',headers=task_headers(r,b),json={}).status_code==200


def test_sealed_bundle_retry_same_bytes_and_bounded_owner_queries(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,raw,spool,state=frozen(r,b,c,ws,tmp_path)
            r.results.prepare(raw,b.task_id,rid,state.bundle.model_dump(mode='json'))
            r.results.failed(raw,b.task_id,rid,{'error_code':'fixture_failed'})
            row=r.results.get(b.task_id,rid)
            r.results.action(b.task_id,rid,'retry',dict(command_id=str(uuid4()),expected_revision=row.revision))
            assert r.results.begin(raw,b.task_id,rid).phase=='verifying'
            assert r.results.prepare(raw,b.task_id,rid,state.bundle.model_dump(mode='json')).bundle==state.bundle
            transfer(r,b,c,spool,state)
            assert r.results.commit(raw,b.task_id,rid).phase=='available'
            path='/api/cloud/v1/tasks/'+b.task_id+'/results/'+rid;headers=owner_headers(r)
            changes=c.get(path+'/changes',headers=headers).json()
            assert {v['path']:v['kind'] for v in changes['changes']}=={'fixture.txt':'deleted','new.py':'added'}
            file=next(f.artifact for f in state.bundle.files if f.role=='artifact')
            preview=c.get(path+'/files/'+file.artifact_id+'/preview',headers=headers,params={'limit':6})
            assert preview.status_code==200,preview.text
            assert preview.json()['text']=='report' and not preview.json()['complete']
            assert c.get(path+'/files/'+file.artifact_id+'/bytes',headers=headers|{'Range':'bytes=0-5'}).content==b'report'
            assert c.get(path+'/files/'+str(uuid4())+'/bytes',headers=headers).status_code==404
            assert c.get(path+'/changes').status_code==401
            assert r.registry.get_task(b.task_id).pending_result_id is None


def test_unsealed_artifact_cannot_use_result_command(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,raw,spool,state=frozen(r,b,c,ws,tmp_path)
            r.results.prepare(raw,b.task_id,rid,state.bundle.model_dump(mode='json'))
            a=state.bundle.files[0].artifact.model_copy(update={'artifact_id':str(uuid4())})
            response=c.post('/internal/cloud/v1/tasks/'+b.task_id+'/artifacts/'+a.artifact_id+'/prepare',headers=task_headers(r,b),json=a.model_dump(mode='json'))
            assert response.status_code==409 and response.json()['error']['code']=='result_slot_invalid'
