"""Owner-only control admission and completion from confirmed task history."""
from uuid import uuid4
import pytest
from starlette.websockets import WebSocketDisconnect
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from test_runner_control import prepared,client,connect,hello


def command(runtime,b,kind='stop',**changes):
    task=runtime.registry.get_task(b.task_id)
    return dict(command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,
        kind=kind,**changes)


def attached(ws,b):
    ws.send_json(hello(b)|{'capabilities':['control:stop','control:recover']})
    return {k:v for k,v in ws.receive_json().items() if k!='kind'}


def test_owner_control_reserves_before_dispatch_deduplicates_and_requires_history(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r,client(r) as c:
        b=prepared(r)
        with connect(c,r,b) as ws:
            fence=attached(ws,b);value=command(r,b);path='/api/cloud/v1/tasks/'+b.task_id+'/control'
            denied=c.post(path,json=value)
            assert denied.status_code==401 and r.registry.get_task(b.task_id).control_operation_id is None
            response=c.post(path,headers=owner_headers(r),json=value)
            assert response.status_code==202
            frame=ws.receive_json();assert frame['kind']=='control_command' and frame['command']['command_id']==value['command_id']
            assert r.registry.get_task(b.task_id).control_operation_id==value['command_id']
            assert c.post(path,headers=owner_headers(r),json=value).json()==response.json()
            assert ws.receive_json()==frame
            conflict=c.post(path,headers=owner_headers(r),json=value|{'kind':'recover','checkpoint_id':'different'})
            assert conflict.status_code==409
            finished=dict(kind='control_finished',command_id=value['command_id'],status='completed',terminal_seq=1,
                phase='ready',quiescent=True,error_code=None,recovery=None)
            ws.send_json(finished|fence)
            with pytest.raises(WebSocketDisconnect):ws.receive_json()
        assert r.registry.get_command(value['command_id']).status=='unknown'
        assert r.registry.get_task(b.task_id).control_operation_id==value['command_id']
        payload={k:v for k,v in finished.items() if k not in ('kind','terminal_seq')}|{'record':'control_finished'}
        r.append_events(b.token.get_secret_value(),dict(task_id=b.task_id,generation=b.generation,epoch=b.state_epoch,
            events=[dict(seq=1,source_generation=b.generation,run_id=value['command_id'].replace('-',''),kind='history_record',payload=payload)]))
        with connect(c,r,b) as ws:
            fence=attached(ws,b)
            assert ws.receive_json()==dict(kind='control_query',command_id=value['command_id'],**fence)
            ws.send_json(finished|fence);ws.send_json(dict(kind='heartbeat',**fence));ws.receive_json()
            assert r.registry.get_command(value['command_id']).status=='succeeded'
            assert r.registry.get_task(b.task_id).control_operation_id is None


def test_stale_or_cross_run_stop_cannot_cancel_current_run(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r,client(r) as c:
        b=prepared(r)
        with connect(c,r,b) as ws:
            attached(ws,b);task=r.registry.get_task(b.task_id)
            run=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='run',model_id='A'))
            assert run.status_code==202;ws.receive_json()
            value=command(r,b,run_id=uuid4().hex)
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/control',headers=owner_headers(r),json=value)
            assert response.status_code==409 and response.json()['error']['code']=='run_identity_mismatch'
            assert r.registry.get_task(b.task_id).control_operation_id is None


def test_background_completion_is_independent_of_root_finish_and_stale_activity(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r,client(r) as c:
        b=prepared(r)
        with connect(c,r,b) as ws:
            fence=attached(ws,b);task=r.registry.get_task(b.task_id)
            cid=str(uuid4())
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=cid,expected_revision=task.revision,expected_generation=task.generation,message='background',model_id='A'))
            run=ws.receive_json()['command'];assert response.status_code==202
            event=dict(seq=1,source_generation=b.generation,run_id=run['run_id'],kind='history_record',
                payload=dict(record='run_finished',command_id=cid,status='completed',session_state='ready'))
            r.append_events(b.token.get_secret_value(),dict(task_id=b.task_id,generation=b.generation,epoch=b.state_epoch,events=[event]))
            ws.send_json(dict(kind='finished',command_id=cid,run_id=run['run_id'],status='completed',terminal_seq=1,quiescent=False,**fence))
            ws.send_json(dict(kind='heartbeat',**fence));ws.receive_json()
            task=r.registry.get_task(b.task_id)
            assert task.active_run_id is None and task.reclaim_blockers==['background_commands']
            assert r.registry.get_command(cid).status=='succeeded'
            ws.send_json(dict(kind='heartbeat',phase='ready',quiescent=True,run_id=uuid4().hex,**fence));ws.receive_json()
            assert r.registry.get_task(b.task_id).reclaim_blockers==['background_commands']
            ws.send_json(dict(kind='heartbeat',phase='ready',quiescent=True,run_id=run['run_id'],**fence));ws.receive_json()
            assert r.registry.get_task(b.task_id).reclaim_blockers==[]


def test_control_reservation_blocks_run_and_normal_environment_stop(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r,client(r) as c:
        b=prepared(r)
        with connect(c,r,b) as ws:
            attached(ws,b)
            value=command(r,b)
            assert c.post('/api/cloud/v1/tasks/'+b.task_id+'/control',headers=owner_headers(r),json=value).status_code==202
            ws.receive_json();task=r.registry.get_task(b.task_id)
            run=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='bad',model_id='A'))
            assert run.status_code==409


def test_explicit_retry_of_unsettled_control_sends_same_identity_without_second_reservation(tmp_path):
    import asyncio
    import httpx
    from naiyue_test_agent.contracts.cloud_runner import Hello
    from naiyue_test_agent.service.cloud.app import create_cloud_apps
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        async def scenario():
            sent=[]
            class Socket:
                async def send_json(self,value):sent.append(value)
                async def close(self,**kwargs):pass
            await r.control.attach(Socket(),b.token.get_secret_value(),Hello.model_validate(
                hello(b)|{'capabilities':['control:stop','control:recover']}))
            owner,_=create_cloud_apps(r)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin,
                    headers=owner_headers(r)) as c:
                value=command(r,b);path='/api/cloud/v1/tasks/'+b.task_id+'/control'
                first=await c.post(path,json=value);revision=r.registry.get_task(b.task_id).revision
                second=await c.post(path,json=value)
                assert first.status_code==second.status_code==202 and first.json()==second.json()
                commands=[f['command'] for f in sent if f['kind']=='control_command']
                assert len(commands)==2 and commands[0]==commands[1]
                assert r.registry.get_task(b.task_id).revision==revision
                assert r.registry.get_task(b.task_id).control_operation_id==value['command_id']
        asyncio.run(scenario())
