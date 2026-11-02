"""Result reservation is atomic with run admission and terminal/recovery protection."""
from uuid import uuid4
import pytest
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from test_runner_control import prepared,client,connect,hello


def run_body(row):
    return dict(command_id=str(uuid4()),expected_revision=row.revision,expected_generation=row.generation,message='work',model_id='A')


def finish(r,b,c,ws,body,*,quiet=True):
    row=r.registry.get_task(b.task_id);result=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=body)
    assert result.status_code==202,result.text
    command=ws.receive_json()['command'];run_id=command['run_id']
    r.append_events(b.token.get_secret_value(),dict(task_id=b.task_id,generation=b.generation,epoch=b.state_epoch,events=[dict(
        seq=1,source_generation=b.generation,run_id=run_id,kind='history_record',payload=dict(record='run_finished',command_id=body['command_id'],status='completed',session_state='ready'))]))
    fence=dict(connection_id=r.registry.get_task(b.task_id).runner_connection_id,generation=b.generation,boot_id=b.boot_id,state_epoch=b.state_epoch)
    ws.send_json(dict(kind='finished',command_id=body['command_id'],run_id=run_id,status='completed',terminal_seq=1,quiescent=quiet,**fence))
    ws.send_json(dict(kind='heartbeat',run_id=run_id,quiescent=quiet,phase='ready',**fence));assert ws.receive_json()['kind']=='welcome'
    return command,fence


def test_terminal_has_durable_unique_pending_result_and_blocks_new_run(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json();body=run_body(r.registry.get_task(b.task_id))
            command,fence=finish(r,b,c,ws,body)
            row=r.registry.get_task(b.task_id);rid=command['result_plan']['result_id']
            assert row.active_run_id is None and row.pending_result_id==rid and 'result_pending' in row.reclaim_blockers
            record=r.results.get(b.task_id,rid)
            assert record.phase=='pending' and record.boundary.seq==1 and record.plan.source_run_id==command['run_id']
            rejected=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=run_body(row))
            assert rejected.status_code==409
            # Only an explicit result action releases this guard, never an observation ACK.
            dropped=r.results.action(b.task_id,rid,'abandon',dict(command_id=str(uuid4()),expected_revision=record.revision))
            assert dropped.status=='succeeded' and r.results.get(b.task_id,rid).phase=='abandoned'
            assert r.registry.get_task(b.task_id).pending_result_id is None


def test_result_waiting_for_background_does_not_occupy_control_slot(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            greeting=hello(b);greeting['capabilities']=['control:stop'];ws.send_json(greeting);ws.receive_json();command,fence=finish(r,b,c,ws,run_body(r.registry.get_task(b.task_id)),quiet=False)
            row=r.registry.get_task(b.task_id)
            assert 'result_pending' in row.reclaim_blockers and 'background_commands' in row.reclaim_blockers
            assert row.artifact_operation_id is None
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/control',headers=owner_headers(r),json=dict(command_id=str(uuid4()),
                expected_revision=row.revision,expected_generation=row.generation,kind='stop',run_id=command['run_id']))
            assert response.status_code==202,response.text
            assert ws.receive_json()['kind']=='control_command'
            assert 'result_pending' in r.registry.get_task(b.task_id).reclaim_blockers


def test_run_reserves_capacity_for_its_result_before_executing(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json()
            with r.database.transaction() as db:count=db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]
            r.settings=r.settings.model_copy(update={'max_receipts':count+1})
            body=run_body(r.registry.get_task(b.task_id));response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=body)
            assert response.status_code==409 and response.json()['error']['code']=='command_capacity'
            assert r.registry.get_task(b.task_id).active_run_id is None
            with pytest.raises(CloudError,match='command_not_found'):r.registry.get_command(body['command_id'])


@pytest.mark.parametrize('phase',['capturing','verifying'])
def test_result_capture_reconnect_does_not_enter_single_artifact_protocol(tmp_path,phase):
    from test_result_api import frozen
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c:
            with connect(c,r,b) as ws:
                rid,raw,spool,state=frozen(r,b,c,ws,tmp_path)
                if phase=='verifying':r.results.prepare(raw,b.task_id,rid,state.bundle.model_dump(mode='json'))
            assert 'artifact_unknown' not in r.registry.get_task(b.task_id).reclaim_blockers
            with connect(c,r,b) as ws:
                ws.send_json(hello(b));welcome=ws.receive_json()
                ws.send_json(dict(kind='heartbeat',**{k:v for k,v in welcome.items() if k!='kind'}))
                # If attach had sent the old artifact_query it would precede this barrier.
                assert ws.receive_json()['kind']=='welcome'
                assert r.results.get(b.task_id,rid).phase==phase
                assert 'artifact_unknown' not in r.registry.get_task(b.task_id).reclaim_blockers


@pytest.mark.parametrize('role,field,code',[('patch','max_patch_bytes','result_patch_budget'),('manifest','max_metadata_bytes','result_metadata_budget')])
def test_owner_independently_checks_result_role_budget(tmp_path,role,field,code):
    from test_result_api import frozen
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,raw,spool,state=frozen(r,b,c,ws,tmp_path)
            r.settings=r.settings.model_copy(update={'result_limits':r.settings.result_limits.model_copy(update={field:1})})
            with pytest.raises(CloudError,match=code):r.results.prepare(raw,b.task_id,rid,state.bundle.model_dump(mode='json'))
            assert r.results.get(b.task_id,rid).bundle is None
