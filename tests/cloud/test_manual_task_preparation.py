"""Manual creation freezes one input and reconciles original remote command IDs."""
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from test_manual_task_bindings import populated,uid
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_task_settings import CloudTaskSettings


class Remote:
    def __init__(self,instance,*,lose=None):
        self.settings=SimpleNamespace(instance_id=instance);self.commands={};self.posts=[];self.task=None;self.lose=lose
        self.uploads=[];self.closed=False
    def upload(self,path):
        import json
        self.uploads.append(path)
        return {'input_id':json.loads((path/'receipt.json').read_text())['input_id'],'uploaded_bytes':0,'cache_hit':True}
    def request(self,method,path,**kwargs):
        if method=='GET' and path.startswith('/commands/'):
            if path.rsplit('/',1)[1] not in self.commands:raise CloudError('command_not_found',404)
            return self.commands[path.rsplit('/',1)[1]]
        if method=='GET' and path.startswith('/tasks/'):return self.task
        assert method=='POST';body=kwargs['json'];self.posts.append((path,body.copy()));cid=body['command_id']
        if path=='/projects':
            result={'project':{'project_id':uid(),'name':body['name'],'revision':1,'created_at':1.0}}
        elif path=='/tasks':
            self.task=dict(task_id=uuid4().hex,project_id=body['project_id'],input_id=body['input_id'],template_id=body['template_id'],
                title='',runner_release_id='r1',state_schema=1,created_at=1.0,updated_at=1.0,revision=1,generation=1,lifecycle='creating',environment_phase='absent',runner_phase='unconfigured',active_run_id=None)
            result={'task':self.task.copy()}
        else:
            assert path.endswith('/commands') and body['kind']=='start'
            self.task.update(revision=2,lifecycle='ready',environment_phase='backend_ready',runner_phase='waiting_model')
            result={'task':self.task.copy()}
        from naiyue_test_agent.contracts.cloud_events import digest
        if path=='/projects':
            operation='project:create';fingerprint=digest(dict(operation=operation,instance_id=self.settings.instance_id,payload={'name':body['name']}));resource=result['project']['project_id']
        elif path=='/tasks':
            operation='task:create';fingerprint=digest(dict(operation=operation,instance_id=self.settings.instance_id,request={k:v for k,v in body.items() if k!='command_id'}));resource=self.task['task_id']
        else:
            operation='environment:start';fingerprint=digest(dict(task_id=self.task['task_id'],request=body,idle_cutoff=None));resource=self.task['task_id']
        receipt=dict(command_id=cid,resource_id=resource,operation=operation,request_digest=fingerprint,status='succeeded',http_status=200,result=result,error_code=None,created_at=1.0,updated_at=1.0)
        self.commands[cid]=receipt
        if self.lose==path or self.lose=='environment' and path.endswith('/commands'):self.lose=None;raise CloudError('cloud_connection_failed',503)
        return {'receipt':receipt} if path=='/projects' else receipt


class Connections:
    def __init__(self,client):self.client=client
    async def get(self,profile_id,expected_instance=None):
        assert expected_instance==self.client.settings.instance_id
        return self.client
    async def close(self):self.client.closed=True
    async def release(self,client):assert client is self.client


def setup(tmp_path,*,lose=None):
    from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks,LocalTaskDomain
    from naiyue_test_agent.application.lifetime import ApplicationLifetime
    from naiyue_test_agent.cloud_files.profiles import ProfileBook,CloudProfile
    store,project,sid=populated(tmp_path);instance=uid();remote=Remote(instance,lose=lose)
    owner=ApplicationLifetime(None,None,AsyncExitStack())
    domain=LocalTaskDomain(owner,store,project)
    profile=CloudProfile(profile_id='work',connection_file=tmp_path/'unused.json',template_ids=['test'],default_template='test')
    manager=ManualCloudTasks(CloudTaskSettings(),book=ProfileBook(profiles=[profile]),connections=Connections(remote))
    manager.connection_settings=lambda p:SimpleNamespace(instance_id=instance)
    return manager,domain,store,project,sid,remote


@pytest.mark.parametrize('lose',[None,'/tasks','environment'])
def test_create_prepares_without_a_model_and_lost_ack_queries_original(tmp_path,lose):
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path)
        try:
            (tmp_path/'source/api.proto').write_text('proto fixture')
            source=await domain.capture(sid,tmp_path/'source',{'schema':1,'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
            remote.lose=lose
            result=await manager.submit(domain,'project',req);await manager.drain()
            op=store.cloud_tasks.operation(req['command_id'],sid)
            if lose is not None:
                assert op['phase']=='unknown'
                await manager.retry(domain,req['command_id'],sid);await manager.drain()
            final=store.cloud_tasks.binding(result['binding']['binding_id'],sid)
            assert final['preparation']=='prepared' and final['task_id']==remote.task['task_id']
            assert final['cloud']['runner_phase']=='waiting_model'
            again=await manager.submit(domain,'project',req);await manager.drain()
            assert again['binding']['binding_id']==final['binding_id']
            assert len([p for p,b in remote.posts if p=='/tasks'])==1
            assert not any('/runs' in p for p,b in remote.posts)
            assert store.sessions.get(sid)['run_id'] is None
            await manager.close();assert remote.closed and remote.task['lifecycle']=='ready'
        finally:store.close()
    asyncio.run(scenario())


def test_cancelled_http_waiter_after_durable_admission_does_not_drop_preparation(tmp_path,monkeypatch):
    import threading
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path);entered=threading.Event();release=threading.Event()
        try:
            (tmp_path/'source/api.proto').write_text('fixture')
            source=await domain.capture(sid,tmp_path/'source',{'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
            original=domain.repo.reserve
            def reserve(*args):
                value=original(*args);entered.set();assert release.wait(5);return value
            monkeypatch.setattr(domain.repo,'reserve',reserve)
            job=asyncio.create_task(manager.submit(domain,'project',req))
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5);job.cancel();release.set()
            with pytest.raises(asyncio.CancelledError):await job
            await manager.drain()
            assert domain.repo.operation(req['command_id'],sid)['phase']=='prepared'
            assert len([p for p,b in remote.posts if p=='/tasks'])==1
            await manager.close()
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_close_waits_for_a_read_before_closing_shared_connection(tmp_path):
    import threading
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path);entered=threading.Event();release=threading.Event()
        try:
            (tmp_path/'source/api.proto').write_text('fixture')
            source=await domain.capture(sid,tmp_path/'source',{'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
            result=await manager.submit(domain,'project',req);await manager.drain();original=remote.request
            def read(method,path,**kwargs):
                if method=='GET' and path.startswith('/tasks/'):
                    entered.set();assert release.wait(5);assert not remote.closed
                return original(method,path,**kwargs)
            remote.request=read
            reader=asyncio.create_task(manager.read(domain,result['binding']['binding_id'],sid))
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5)
            closer=asyncio.create_task(manager.close());await asyncio.sleep(0)
            assert not remote.closed and not closer.done()
            release.set();await reader;await closer;assert remote.closed
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_cancel_during_accepted_task_with_lost_ack_can_read_only_reconcile(tmp_path):
    import threading
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path);entered=threading.Event();release=threading.Event()
        try:
            (tmp_path/'source/api.proto').write_text('fixture')
            source=await domain.capture(sid,tmp_path/'source',{'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
            original=remote.request
            def lost(method,path,**kwargs):
                value=original(method,path,**kwargs)
                if method=='POST' and path=='/tasks':
                    entered.set();assert release.wait(5);raise CloudError('cloud_connection_failed',503)
                return value
            remote.request=lost
            result=await manager.submit(domain,'project',req)
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5)
            await manager.cancel(domain,req['command_id'],sid);release.set();await manager.drain()
            binding=domain.repo.binding(result['binding']['binding_id'],sid)
            assert binding['retained'] and binding['task_id'] is None
            await manager.retry(domain,req['command_id'],sid)
            binding=domain.repo.binding(binding['binding_id'],sid)
            assert binding['task_id']==remote.task['task_id'] and binding['preparation']=='cancelled'
            assert len(remote.posts)==2 and not any(p.endswith('/commands') for p,b in remote.posts)
            await manager.close()
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_history_proxy_uses_only_the_bound_task_and_does_not_change_local_history(tmp_path):
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path)
        try:
            (tmp_path/'source/api.proto').write_text('fixture')
            src=await domain.capture(sid,tmp_path/'source',{'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=src['source_handle'],template_id='test')
            result=await manager.submit(domain,'project',req);await manager.drain();calls=[];original=remote.request
            def history(method,path,**kwargs):
                if path.endswith('/history'):
                    calls.append((method,path,kwargs));return {'messages':[]}
                return original(method,path,**kwargs)
            remote.request=history
            value=await manager.view(domain,result['binding']['binding_id'],sid,'history',{'limit':10})
            assert value=={'messages':[]}
            assert calls==[('GET','/tasks/'+remote.task['task_id']+'/history',{'params':{'limit':10}})]
            assert store.sessions.get(sid)['run_id'] is None
            with pytest.raises(CloudError,match='cloud_view_not_found'):
                await manager.view(domain,result['binding']['binding_id'],sid,'../runs',{})
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_capacity_and_cancel_before_task_dispatch_leave_no_cloud_task(tmp_path):
    import threading
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path);entered=threading.Event();release=threading.Event()
        manager.settings=manager.settings.model_copy(update={'max_preparations':1})
        try:
            (tmp_path/'source/api.proto').write_text('fixture')
            src=await domain.capture(sid,tmp_path/'source',{'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=src['source_handle'],template_id='test')
            original=remote.upload
            def upload(path):entered.set();assert release.wait(5);return original(path)
            remote.upload=upload
            result=await manager.submit(domain,'project',req)
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5)
            with pytest.raises(CloudError,match='cloud_preparation_capacity'):
                await manager.submit(domain,'project',req|{'command_id':uid()})
            same=await manager.submit(domain,'project',req)
            assert same['binding']['binding_id']==result['binding']['binding_id']
            assert len(domain.repo.list(sid))==1
            await manager.cancel(domain,req['command_id'],sid)
            with pytest.raises(SessionPersistenceError,match='session_cloud_tasks_retained'):store.runs.delete(sid,())
            release.set();await manager.drain()
            assert remote.task is None
            assert not domain.repo.binding(result['binding']['binding_id'],sid)['retained']
            store.runs.delete(sid,());await manager.close()
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_confirmed_remote_quota_rejection_can_be_cancelled_without_phantom_task(tmp_path):
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path)
        try:
            (tmp_path/'source/api.proto').write_text('fixture')
            src=await domain.capture(sid,tmp_path/'source',{'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=src['source_handle'],template_id='test')
            original=remote.request
            def reject(method,path,**kwargs):
                if method=='POST' and path=='/tasks':raise CloudError('environment_retained_capacity',409)
                return original(method,path,**kwargs)
            remote.request=reject
            result=await manager.submit(domain,'project',req);await manager.drain()
            op=domain.repo.operation(req['command_id'],sid)
            assert op['phase']=='failed' and op['error_code']=='environment_retained_capacity'
            await manager.cancel(domain,req['command_id'],sid)
            assert not domain.repo.binding(result['binding']['binding_id'],sid)['retained']
            store.runs.delete(sid,());await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_cancel_and_producer_settlement_interleave_without_permanent_false_guard(tmp_path,monkeypatch):
    import threading
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path)
        entered=threading.Event();upload_release=threading.Event();cancel_entered=threading.Event();cancel_release=threading.Event()
        try:
            (tmp_path/'source/api.proto').write_text('fixture')
            source=await domain.capture(sid,tmp_path/'source',{'paths':['api.proto']},manager.settings.input_limits)
            req=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
            def upload(path):entered.set();assert upload_release.wait(5);raise CloudError('input_not_ready')
            remote.upload=upload;original=domain.repo.cancel
            def cancel(*args,**kwargs):
                if not kwargs.get('settled',True):cancel_entered.set();assert cancel_release.wait(5)
                return original(*args,**kwargs)
            monkeypatch.setattr(domain.repo,'cancel',cancel)
            result=await manager.submit(domain,'project',req)
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5)
            cancelling=asyncio.create_task(manager.cancel(domain,req['command_id'],sid))
            await asyncio.wait_for(asyncio.to_thread(cancel_entered.wait),5)
            upload_release.set();await manager.drain();cancel_release.set();await cancelling
            assert not domain.repo.binding(result['binding']['binding_id'],sid)['retained']
            assert remote.task is None
            await manager.close()
        finally:upload_release.set();cancel_release.set();store.close()
    asyncio.run(scenario())


def test_pending_environment_reconciles_original_command_only_and_checks_digest_first():
    from naiyue_test_agent.application.cloud_local.preparation import receipt
    from naiyue_test_agent.contracts.cloud_events import digest
    body={'command_id':uid()};fingerprint=digest({'fixture':True});calls=[]
    value=dict(command_id=body['command_id'],resource_id='a'*32,operation='environment:start',request_digest=fingerprint,
        status='unknown',http_status=202,result={},created_at=1.0,updated_at=1.0)
    class Client:
        def request(self,method,path,**kwargs):
            calls.append((method,path,kwargs))
            return value if method=='GET' else value|{'status':'succeeded','http_status':200}
    result=receipt(Client(),'/tasks/'+'a'*32+'/commands',body,'environment:start',fingerprint,reconcile=True)
    assert result.status=='succeeded'
    assert calls==[('GET','/commands/'+body['command_id'],{}),('POST','/commands/'+body['command_id']+'/reconcile',{'json':{}})]
    calls.clear();value['request_digest']='0'*64
    with pytest.raises(CloudError,match='cloud_receipt_mismatch'):
        receipt(Client(),'/tasks/'+'a'*32+'/commands',body,'environment:start',fingerprint,reconcile=True)
    assert len(calls)==1
