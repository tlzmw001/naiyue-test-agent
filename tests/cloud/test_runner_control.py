"""Control authentication, durable admission and completion from confirmed history."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.directories import TaskDirectories
from naiyue_test_agent.contracts.cloud_runner import Hello
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import owner_headers
from lifecycle_helpers import config,register,template


def prepared(runtime):
    record=register(runtime)
    TaskDirectories(runtime.settings.data_dir).prepare(record,template())
    b=runtime.control.prepare(record.task_id)
    task=runtime.registry.get_task(record.task_id)
    runtime.replace_task(task.model_copy(update={'revision':task.revision+1,'environment_phase':'backend_ready',
        'input_ready':True,'sandbox_id':'fixture-sandbox','lifecycle':'starting'}),
        expected_revision=task.revision,expected_generation=task.generation)
    return b


def hello(b,phase='ready'):
    value={k:getattr(b,k) for k in ('task_id','project_id','input_id','instance_id','generation','boot_id','state_epoch','release_id')}
    return Hello(kind='hello',phase=phase,model_ids=['A'] if phase=='ready' else [],**value).model_dump()


def client(runtime):
    owner,task=create_cloud_apps(runtime)
    # One portal/event loop, while the actual Host/authorization checks stay intact.
    owner.router.routes.extend(task.router.routes)
    return TestClient(owner,base_url=runtime.settings.origin)


def connect(c,r,b,**changes):
    headers={'Host':r.settings.task_listen,'Authorization':'Bearer '+b.token.get_secret_value()}|changes
    return c.websocket_connect('/internal/cloud/v1/tasks/'+b.task_id+'/control',headers=headers)


def test_completion_requires_history_and_duplicate_owner_command_does_not_dispatch(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));welcome=ws.receive_json();fence={k:v for k,v in welcome.items() if k!='kind'}
            task=r.registry.get_task(b.task_id);assert task.lifecycle=='ready'
            value=dict(command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='work',model_id='A')
            path='/api/cloud/v1/tasks/'+b.task_id+'/runs'
            receipt=c.post(path,headers=owner_headers(r),json=value).json()
            assert receipt['status']=='running' and receipt['result']['phase']=='dispatched'
            dispatch=ws.receive_json();command=dispatch['command'];run_id=command['run_id']
            assert c.post(path,headers=owner_headers(r),json=value).json()==receipt
            ws.send_json(dict(kind='accepted',command_id=value['command_id'],run_id=run_id,status='running',**fence))
            # A task cannot claim success from its WS acknowledgement alone.
            ws.send_json(dict(kind='finished',command_id=value['command_id'],run_id=run_id,status='completed',terminal_seq=1,quiescent=True,**fence))
            with pytest.raises(WebSocketDisconnect):ws.receive_json()
            task=r.registry.get_task(b.task_id)
            assert task.active_run_id==run_id and 'unknown' in task.reclaim_blockers
            assert r.registry.get_command(value['command_id']).status=='unknown'
        event=dict(seq=1,source_generation=b.generation,run_id=run_id,kind='history_record',
            payload=dict(record='run_finished',command_id=value['command_id'],status='completed',session_state='ready'))
        r.append_events(b.token.get_secret_value(),dict(task_id=b.task_id,generation=b.generation,epoch=b.state_epoch,events=[event]))
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));welcome=ws.receive_json();fence={k:v for k,v in welcome.items() if k!='kind'}
            assert ws.receive_json()['kind']=='query'
            ws.send_json(dict(kind='finished',command_id=value['command_id'],run_id=run_id,status='completed',terminal_seq=1,quiescent=True,**fence))
            ws.send_json(dict(kind='heartbeat',**fence))
            assert ws.receive_json()['kind']=='welcome'  # Processing barrier, not sleep.
            assert r.registry.get_command(value['command_id']).status=='succeeded'
            assert r.registry.get_task(b.task_id).active_run_id is None
            assert r.history_page(b.task_id)['events'][0]['payload']==event['payload']


def test_control_auth_waiting_model_fences_rotation_and_strict_json(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c:
            with pytest.raises(WebSocketDisconnect):
                with connect(c,r,b,Authorization='Bearer wrong'):pass
            with connect(c,r,b) as ws:
                ws.send_json(hello(b,'waiting_model'));welcome=ws.receive_json();fence={k:v for k,v in welcome.items() if k!='kind'}
                task=r.registry.get_task(b.task_id)
                response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                    command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='work',model_id='A'))
                assert response.status_code==409 and response.json()['error']['code']=='model_unavailable'
                ws.send_json(dict(kind='renew',**fence));renewed=ws.receive_json()
                assert renewed['kind']=='renewed' and renewed['token']!=b.token.get_secret_value()
                with pytest.raises(Exception,match='unauthorized'):r.credentials.authorize(b.token.get_secret_value(),b.task_id,'control:connect')
                ws.send_text('{"kind":"heartbeat","kind":"renew"}')
                with pytest.raises(WebSocketDisconnect):ws.receive_json()


def test_persisted_connection_id_after_restart_is_not_live_admission(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r);task=r.registry.get_task(b.task_id)
        r.replace_task(task.model_copy(update={'revision':task.revision+1,'lifecycle':'ready',
            'runner_phase':'ready','runner_connection_id':str(uuid4()),'runner_model_ids':['A']}),
            expected_revision=task.revision,expected_generation=task.generation)
        with client(r) as c:
            task=r.registry.get_task(b.task_id);command_id=str(uuid4())
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=command_id,expected_revision=task.revision,expected_generation=task.generation,message='work',model_id='A'))
            assert response.status_code==409 and response.json()['error']['code']=='runner_not_ready'
            assert r.registry.get_task(b.task_id).active_run_id is None
            with pytest.raises(Exception,match='command_not_found'):r.registry.get_command(command_id)


def test_new_connection_fences_old_connection_without_cancelling_task(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as first:
            first.send_json(hello(b));old=first.receive_json()
            with connect(c,r,b) as second:
                second.send_json(hello(b));new=second.receive_json()
                assert new['connection_id']!=old['connection_id']
                with pytest.raises(WebSocketDisconnect):first.receive_json()
                second.send_json(dict(kind='heartbeat',**{k:v for k,v in new.items() if k!='kind'}))
                assert second.receive_json()['connection_id']==new['connection_id']
                assert r.registry.get_task(b.task_id).runner_connection_id==new['connection_id']


@pytest.mark.parametrize('boundary',['registry','file'])
def test_bootstrap_interrupted_publication_is_retryable(tmp_path,monkeypatch,boundary):
    import naiyue_test_agent.application.cloud.runner_bootstrap as module
    with open_cloud_runtime(config(tmp_path)) as r:
        record=register(r);TaskDirectories(r.settings.data_dir).prepare(record,template())
        owner,name=(r.registry,'save_task_cas') if boundary=='registry' else (module,'write_json')
        original=getattr(owner,name)
        def fail(*args,**kwargs):raise OSError('injected bootstrap publication interruption')
        monkeypatch.setattr(owner,name,fail)
        with pytest.raises(OSError):r.control.prepare(record.task_id)
        reserved=r.registry.get_task(record.task_id)
        monkeypatch.setattr(owner,name,original)
        b=r.control.prepare(record.task_id)
        assert b.boot_id==r.registry.get_task(record.task_id).runner_boot_id
        if reserved.runner_boot_id:assert b.boot_id==reserved.runner_boot_id and b.state_epoch==reserved.state_epoch


def test_expired_control_credential_cannot_admit_new_run(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json()
            with r.database.transaction(write=True) as db:db.execute('UPDATE task_credentials SET expires_at=1')
            task=r.registry.get_task(b.task_id)
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='work',model_id='A'))
            assert response.status_code==409 and response.json()['error']['code']=='runner_not_ready'
            assert r.registry.get_task(b.task_id).active_run_id is None


def test_dispatch_final_envelope_is_budgeted_before_reservation(tmp_path):
    with open_cloud_runtime(config(tmp_path,max_frame_bytes=4096)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json();task=r.registry.get_task(b.task_id)
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='x'*3880,model_id='A'))
            assert response.status_code==413
            assert r.registry.get_task(b.task_id).active_run_id is None


def test_concurrent_attach_is_reserved_before_database_await(tmp_path,monkeypatch):
    import asyncio
    from threading import Event
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    class Socket:
        def __init__(self):self.closed=False;self.messages=[]
        async def send_json(self,value):self.messages.append(value)
        async def close(self,**kwargs):self.closed=True
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r);entered,release=Event(),Event();original=r.control.bind
        def delayed(connection,value):
            task=original(connection,value);entered.set();assert release.wait(5);return task
        monkeypatch.setattr(r.control,'bind',delayed)
        async def scenario():
            first,second=Socket(),Socket();value=Hello.model_validate(hello(b));raw=b.token.get_secret_value()
            pending=asyncio.create_task(r.control.attach(first,raw,value))
            try:
                assert await asyncio.to_thread(entered.wait,5)
                with pytest.raises(CloudError,match='runner_connect_busy'):
                    await r.control.attach(second,raw,value)
            finally:release.set()
            connection=await pending
            assert r.control.connections[b.task_id] is connection and not first.closed
            assert r.registry.get_task(b.task_id).runner_connection_id==connection.connection_id
            await r.control.detach(connection)
        asyncio.run(scenario())


def test_bootstrap_carries_explicit_permission_policy(tmp_path):
    from naiyue_test_agent.tools.permission_rules import PermissionSettings,PermissionRule
    policy=PermissionSettings(mode='NoAsk',deny=(PermissionRule(tool='bash'),))
    with open_cloud_runtime(config(tmp_path,runner_permissions=policy)) as r:
        b=prepared(r)
        assert b.permissions==policy


def test_cancelled_attach_retains_reservation_until_real_bind_finishes(tmp_path,monkeypatch):
    import asyncio
    from threading import Event
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    class Socket:
        async def send_json(self,value):pass
        async def close(self,**kwargs):pass
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r);entered,release,settled=Event(),Event(),Event();original=r.control.bind
        def delayed(connection,value):
            entered.set();assert release.wait(5)
            try:return original(connection,value)
            finally:settled.set()
        monkeypatch.setattr(r.control,'bind',delayed)
        async def scenario():
            value=Hello.model_validate(hello(b));raw=b.token.get_secret_value()
            pending=asyncio.create_task(r.control.attach(Socket(),raw,value))
            try:
                assert await asyncio.to_thread(entered.wait,5)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):await pending
                with pytest.raises(CloudError,match='runner_connect_busy'):await r.control.attach(Socket(),raw,value)
            finally:
                release.set();assert await asyncio.to_thread(settled.wait,5)
            await asyncio.gather(*r.control.attaching.values())
            monkeypatch.setattr(r.control,'bind',original)
            latest=await r.control.attach(Socket(),raw,value)
            assert r.registry.get_task(b.task_id).runner_connection_id==latest.connection_id
            assert r.control.connections[b.task_id] is latest
            await r.control.detach(latest)
        asyncio.run(scenario())


def test_queued_send_rechecks_credential_after_acquiring_send_lock(tmp_path,monkeypatch):
    import asyncio
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    class Socket:
        def __init__(self):self.messages=[]
        async def send_json(self,value):self.messages.append(value)
        async def close(self,**kwargs):pass
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        async def scenario():
            socket=Socket();connection=await r.control.attach(socket,b.token.get_secret_value(),Hello.model_validate(hello(b)))
            socket.messages.clear();entered=asyncio.Event()
            class Gate:
                def __init__(self):self.lock=asyncio.Lock()
                async def __aenter__(self):entered.set();await self.lock.acquire()
                async def __aexit__(self,*args):self.lock.release()
            gate=Gate();await gate.lock.acquire();connection.lock=gate
            pending=asyncio.create_task(connection.send({'kind':'welcome'}))
            await entered.wait()
            with r.database.transaction(write=True) as c:c.execute('UPDATE task_credentials SET expires_at=1')
            gate.lock.release()
            with pytest.raises(CloudError,match='unauthorized'):await pending
            assert socket.messages==[]
            await r.control.detach(connection)
        asyncio.run(scenario())


def test_idle_disconnect_blocks_reclaim_until_new_ready_boundary(tmp_path):
    import asyncio
    from naiyue_test_agent.application.cloud.lifecycle import CloudLifecycle
    class Socket:
        async def send_json(self,value):pass
        async def close(self,**kwargs):pass
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        async def scenario():
            first=await r.control.attach(Socket(),b.token.get_secret_value(),Hello.model_validate(hello(b)))
            await r.control.detach(first)
            task=r.registry.get_task(b.task_id)
            assert 'runner_disconnected' in task.reclaim_blockers and task.last_quiescent_at is None
            assert not CloudLifecycle.idle(task,9999999999)
            second=await r.control.attach(Socket(),b.token.get_secret_value(),Hello.model_validate(hello(b)))
            task=r.registry.get_task(b.task_id)
            assert task.reclaim_blockers==[] and task.last_quiescent_at is not None
            await r.control.detach(second)
        asyncio.run(scenario())


def test_runner_ready_before_environment_finish_still_records_quiescence(tmp_path):
    import asyncio
    from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
    from lifecycle_helpers import Backend,command
    class Socket:
        async def send_json(self,value):pass
        async def close(self,**kwargs):pass
    with open_cloud_runtime(config(tmp_path)) as r:
        class EarlyReady(Backend):
            async def install(self,sandbox_id,input_id,limits):
                result=await super().install(sandbox_id,input_id,limits)
                task_id=next(iter(self.sandboxes.values())).metadata['nyt-task']
                b=r.control.prepare(task_id)
                await r.control.attach(Socket(),b.token.get_secret_value(),Hello.model_validate(hello(b)))
                return result
        async def scenario():
            async with open_cloud_backend(r,backend=EarlyReady()):
                record=register(r)
                assert (await r.lifecycle.command(record.task_id,command(record))).status=='succeeded'
                task=r.registry.get_task(record.task_id)
                assert task.lifecycle=='ready' and task.last_quiescent_at is not None
                assert (await r.lifecycle.command(record.task_id,command(task,'stop_environment'))).status=='succeeded'
        asyncio.run(scenario())
