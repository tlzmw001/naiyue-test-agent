"""Manual run/control journals freeze intent and keep task drafts independent."""
import asyncio
import pytest
from test_manual_task_preparation import setup
from test_manual_task_bindings import uid


async def prepared(tmp_path):
    manager,domain,store,project,sid,remote=setup(tmp_path)
    (tmp_path/'source/test.py').write_text('fixture')
    source=await domain.capture(sid,tmp_path/'source',{'paths':['test.py']},manager.settings.input_limits)
    request=dict(command_id=uid(),local_session_id=sid,profile_id='work',source_handle=source['source_handle'],template_id='test')
    result=await manager.submit(domain,'project',request);await manager.drain()
    remote.task.update(runner_phase='ready',runner_model_ids=['A'],runner_capabilities=['control:stop','control:recover'],
        reclaim_blockers=[],last_quiescent_at=1.0)
    binding=await manager.read(domain,result['binding']['binding_id'],sid)
    return manager,domain,store,sid,remote,binding


def test_unchanged_task_read_does_not_invalidate_binding_version(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            again=await manager.read(domain,binding['binding_id'],sid)
            assert again['revision']==binding['revision']
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_drafts_have_separate_cas_and_sending_freezes_original_text(tmp_path):
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            journal=domain.repo.commands;bid=binding['binding_id']
            draft=journal.save_draft(bid,sid,dict(expected_draft_revision=0,text='first',model_id='A'))
            assert draft['binding_id']==bid and draft['draft_revision']==1
            with pytest.raises(CloudError,match='cloud_draft_conflict'):
                journal.save_draft(bid,sid,dict(expected_draft_revision=0,text='old',model_id='A'))
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='first',model_id='A',draft_revision=1)
            op=journal.reserve(bid,sid,'message',req)
            assert journal.reserve(bid,sid,'message',req)==op
            with pytest.raises(CloudError,match='cloud_command_conflict'):
                journal.reserve(bid,sid,'message',req|{'text':'changed'})
            journal.save_draft(bid,sid,dict(expected_draft_revision=1,text='new draft',model_id='A'))
            assert journal.draft(bid,sid)['text']=='new draft'
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


class CommandRemote:
    """Owner-shaped durable ACKs; no model or real service request."""
    def __init__(self,remote):self.remote=remote;self.calls=[];self.lose=False
    def request(self,method,path,**kwargs):
        from uuid import uuid4
        from naiyue_test_agent.contracts.cloud_events import digest
        task=self.remote.task
        if method!='POST' or not path.endswith(('/runs','/control')):
            return self.remote.request(method,path,**kwargs)
        body=kwargs['json'];self.calls.append((path,body.copy()))
        if body['command_id'] in self.remote.commands:return self.remote.commands[body['command_id']]
        if path.endswith('/runs'):
            assert task['active_run_id'] is None
            run=uuid4().hex;command=dict(command_id=body['command_id'],run_id=run,message=body['message'],model_id=body['model_id'],thinking_level=body.get('thinking_level','model_default'))
            task.update(active_run_id=run,last_run_id=run,revision=task['revision']+1,reclaim_blockers=['active_run','required_history'])
            operation='run:start';status='running'
        else:
            assert body['run_id']==task.get('active_run_id') or body['run_id']==task.get('last_run_id')
            command=dict(command_id=body['command_id'],kind=body['kind'],run_id=body['run_id'],
                target_command_id=next((c for c,r in self.remote.commands.items() if r.get('result',{}).get('command',{}).get('run_id')==body['run_id']),None),checkpoint_id=body.get('checkpoint_id'))
            task.update(active_run_id=None,revision=task['revision']+1,reclaim_blockers=[],last_quiescent_at=2.0,runner_phase='ready')
            operation='control:'+body['kind'];status='succeeded'
        value=dict(command_id=body['command_id'],resource_id=task['task_id'],operation=operation,
            expected_generation=body['expected_generation'],request_digest=digest(dict(task_id=task['task_id'],request=body)),
            status=status,http_status=202 if status=='running' else 200,result={'command':command},created_at=1.0,updated_at=2.0)
        self.remote.commands[body['command_id']]=value
        if self.lose:
            self.lose=False
            from naiyue_test_agent.contracts.cloud_tasks import CloudError
            raise CloudError('cloud_connection_failed',503)
        return value


def test_first_message_ack_is_accepted_and_followup_reuses_task(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            adapter=CommandRemote(remote);manager.connections.client=adapter
            adapter.settings=remote.settings
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',draft_revision=None)
            op=await manager.commands.submit(domain,binding['binding_id'],'message',req);await manager.drain()
            saved=domain.repo.operation(op['command_id'],sid)
            assert saved['phase']=='accepted' and saved['run_id']==remote.task['active_run_id']
            await manager.commands.submit(domain,binding['binding_id'],'message',req);await manager.drain()
            assert len(adapter.calls)==1
            original_task=remote.task['task_id'];remote.task.update(active_run_id=None,reclaim_blockers=[],last_quiescent_at=2.0,revision=remote.task['revision']+1)
            fresh=await manager.read(domain,binding['binding_id'],sid)
            await manager.commands.submit(domain,binding['binding_id'],'message',req|{'command_id':uid(),'text':'next','expected_binding_revision':fresh['revision']})
            await manager.drain();assert len(adapter.calls)==2 and remote.task['task_id']==original_task
            assert len(remote.uploads)==1 and store.sessions.get(sid)['run_id'] is None
            await manager.close();assert remote.task['active_run_id'] is not None
        finally:store.close()
    asyncio.run(scenario())


def test_force_release_confirmation_cannot_rebase_to_new_work_in_same_generation(tmp_path):
    from naiyue_test_agent.application.cloud_local.command_dispatch import execute
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            request=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],
                action='release',acknowledge_unknown=True,expected_generation=remote.task['generation'])
            op=domain.repo.commands.reserve(binding['binding_id'],sid,'release',request)
            count=len(remote.posts);remote.task.update(active_run_id='b'*32,revision=remote.task['revision']+1)
            with pytest.raises(CloudError,match='cloud_confirmation_changed'):
                execute(domain,remote,op,manager.settings)
            assert len(remote.posts)==count
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_operation_index_is_bounded_without_echoing_saved_message_bodies(tmp_path):
    import json
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            text='x'*100000
            for _ in range(12):
                req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text=text,model_id='A',draft_revision=None)
                op=domain.repo.commands.reserve(binding['binding_id'],sid,'message',req)
                domain.repo.commands.fail(op['command_id'],sid,'rejected','model_unavailable')
            values=await manager.commands.list(domain,binding['binding_id'],sid)
            assert len(values)==13 and len(json.dumps(values))<65536
            assert text not in json.dumps(values)
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_late_ack_preserves_new_draft_and_matching_ack_clears_only_its_version(tmp_path):
    import threading
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path);entered=threading.Event();release=threading.Event()
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
            original=adapter.request
            def held(method,path,**kwargs):
                value=original(method,path,**kwargs)
                if method=='POST' and path.endswith('/runs'):entered.set();assert release.wait(5)
                return value
            adapter.request=held;bid=binding['binding_id'];journal=domain.repo.commands
            journal.save_draft(bid,sid,dict(expected_draft_revision=0,text='first',model_id='A'))
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='first',model_id='A',draft_revision=1)
            await manager.commands.submit(domain,bid,'message',req)
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5)
            journal.save_draft(bid,sid,dict(expected_draft_revision=1,text='second',model_id='A'))
            release.set();await manager.drain();assert journal.draft(bid,sid)['text']=='second'
            remote.task.update(active_run_id=None,reclaim_blockers=[],last_quiescent_at=2.0,revision=remote.task['revision']+1)
            binding=await manager.read(domain,bid,sid)
            await manager.commands.submit(domain,bid,'message',req|{'command_id':uid(),'text':'second','draft_revision':2,'expected_binding_revision':binding['revision']})
            await manager.drain();draft=journal.draft(bid,sid)
            assert draft['text']=='' and draft['draft_revision']==3
            await manager.close()
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_pending_message_cannot_be_overtaken_by_stop_or_release(tmp_path):
    import threading
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path);entered=threading.Event();release=threading.Event()
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter;original=adapter.request
            def held(method,path,**kwargs):
                if method=='POST' and path.endswith('/runs'):entered.set();assert release.wait(5)
                return original(method,path,**kwargs)
            adapter.request=held;req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='first',model_id='A',draft_revision=None)
            await manager.commands.submit(domain,binding['binding_id'],'message',req)
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5);assert remote.task['active_run_id'] is None
            base=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'])
            with pytest.raises(CloudError,match='cloud_command_pending'):
                await manager.commands.submit(domain,binding['binding_id'],'stop',base|dict(kind='stop',run_id=None,checkpoint_id=None))
            with pytest.raises(CloudError,match='cloud_command_pending'):
                await manager.commands.submit(domain,binding['binding_id'],'release',base|dict(action='release',acknowledge_unknown=False,expected_generation=None))
            release.set();await manager.drain();await manager.close()
            assert len(adapter.calls)==1
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_recover_uses_original_failed_run_without_a_new_model_command(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='first',model_id='A',draft_revision=None)
            await manager.commands.submit(domain,binding['binding_id'],'message',req);await manager.drain()
            run=remote.task['active_run_id'];remote.task.update(active_run_id=None,runner_phase='blocked',
                reclaim_blockers=['recovery_required'],runner_recovery={'run_id':run,'checkpoint_id':'cp'},revision=remote.task['revision']+1)
            remote.commands[req['command_id']]=remote.commands[req['command_id']]|{'status':'failed','http_status':200}
            binding=await manager.read(domain,binding['binding_id'],sid)
            recovery=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],kind='recover',run_id=run,checkpoint_id='cp')
            op=await manager.commands.submit(domain,binding['binding_id'],'recover',recovery);await manager.drain()
            assert domain.repo.operation(op['command_id'],sid)['phase']=='succeeded'
            assert len([p for p,b in adapter.calls if p.endswith('/runs')])==1
            assert adapter.calls[-1][1]['checkpoint_id']=='cp'
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('field',['command_id','resource_id','request_digest','expected_generation'])
def test_wrong_receipt_does_not_clear_draft_or_confirm_run(tmp_path,field):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter;original=adapter.request
            def wrong(method,path,**kwargs):
                value=original(method,path,**kwargs)
                if method=='POST' and path.endswith('/runs'):
                    return value|{field:2 if field=='expected_generation' else '0'*64 if field=='request_digest' else uid()}
                return value
            adapter.request=wrong;bid=binding['binding_id'];domain.repo.commands.save_draft(bid,sid,dict(expected_draft_revision=0,text='first',model_id='A'))
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='first',model_id='A',draft_revision=1)
            op=await manager.commands.submit(domain,bid,'message',req);await manager.drain()
            assert domain.repo.operation(op['command_id'],sid)['phase']=='unknown'
            assert domain.repo.commands.draft(bid,sid)['text']=='first'
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_runner_wait_timeout_reaches_actual_http_request_without_sleep(tmp_path):
    import json
    import httpx
    from naiyue_test_agent.cloud_files.client import CloudClient,CloudConnection
    from naiyue_test_agent.contracts.cloud_events import digest
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    from naiyue_test_agent.application.cloud_local.command_dispatch import execute
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path);timeouts=[];started=False;receipt=None
        try:
            remote.task.update(environment_phase='absent',lifecycle='stopped',runner_phase='unconfigured')
            request=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',draft_revision=None)
            op=domain.repo.commands.reserve(binding['binding_id'],sid,'message',request)
            def handler(req):
                nonlocal started,receipt
                if req.method=='GET' and '/commands/' in req.url.path:
                    if receipt:return httpx.Response(200,json=receipt)
                    return httpx.Response(404,json={'error':{'code':'command_not_found'}})
                if req.method=='POST':
                    body=json.loads(req.content);started=True;remote.task.update(environment_phase='backend_ready',runner_phase='booting')
                    receipt=dict(command_id=body['command_id'],resource_id=remote.task['task_id'],operation='environment:start',
                        expected_generation=body['expected_generation'],request_digest=digest(dict(task_id=remote.task['task_id'],request=body,idle_cutoff=None)),
                        status='succeeded',http_status=200,result={},created_at=1.0,updated_at=1.0)
                    return httpx.Response(200,json=receipt)
                if started:
                    timeouts.append(req.extensions['timeout']);raise httpx.ReadTimeout('controlled timeout',request=req)
                return httpx.Response(200,json=remote.task)
            client=CloudClient(CloudConnection(endpoint='http://127.0.0.1:43121',owner_authority='127.0.0.1:43122',owner_token='fixture',instance_id=remote.settings.instance_id),transport=httpx.MockTransport(handler))
            try:
                with pytest.raises(CloudError,match='cloud_connection_failed'):
                    execute(domain,client,op,manager.settings.model_copy(update={'runner_wait_seconds':0.25}))
                assert timeouts and all(0<v<=0.25 for v in timeouts[0].values())
                assert 'request' not in domain.repo.operation(op['command_id'],sid)['steps']['primary']
            finally:client.close()
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_waiting_model_is_explicit_and_never_sends_a_run(tmp_path):
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            remote.task.update(runner_phase='waiting_model',runner_model_ids=[])
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',draft_revision=None)
            op=await manager.commands.submit(domain,binding['binding_id'],'message',req);await manager.drain()
            assert domain.repo.operation(op['command_id'],sid)['error_code']=='model_unavailable'
            assert adapter.calls==[]
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_stop_has_capacity_while_a_message_ack_is_still_in_flight(tmp_path):
    import threading
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path);entered=threading.Event();release=threading.Event()
        manager.settings=manager.settings.model_copy(update={'max_commands':1,'max_controls':1})
        try:
            adapter=CommandRemote(remote);adapter.settings=remote.settings;manager.connections.client=adapter;original=adapter.request
            def held(method,path,**kwargs):
                value=original(method,path,**kwargs)
                if method=='POST' and path.endswith('/runs'):entered.set();assert release.wait(5)
                return value
            adapter.request=held
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',draft_revision=None)
            await manager.commands.submit(domain,binding['binding_id'],'message',req)
            await asyncio.wait_for(asyncio.to_thread(entered.wait),5)
            binding=await manager.read(domain,binding['binding_id'],sid)
            stop=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],kind='stop',run_id=remote.task['active_run_id'],checkpoint_id=None)
            op=await manager.commands.submit(domain,binding['binding_id'],'stop',stop)
            job=manager.commands.flights.get((domain.project_id,op['command_id']))
            if job:await job[1]
            assert domain.repo.operation(op['command_id'],sid)['phase']=='succeeded'
            assert not release.is_set()
            release.set();await manager.drain();await manager.close()
        finally:release.set();store.close()
    asyncio.run(scenario())


def test_receipt_state_does_not_depend_on_wall_clock_order(tmp_path):
    from naiyue_test_agent.application.cloud_local.command_dispatch import fixed
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',draft_revision=None)
            op=domain.repo.commands.reserve(binding['binding_id'],sid,'message',req)
            body=dict(command_id=op['command_id'],expected_revision=remote.task['revision'],expected_generation=remote.task['generation'],message='work',model_id='A')
            op=fixed(domain,op,'primary',body,'run:start');domain.repo.commands.dispatch(op['command_id'],sid,'primary')
            value=dict(command_id=op['command_id'],resource_id=remote.task['task_id'],operation='run:start',
                request_digest=op['steps']['primary']['request_digest'],expected_generation=1,status='running',http_status=202,
                result={'command':dict(command_id=op['command_id'],run_id='c'*32,message='work',model_id='A')},created_at=1.0,updated_at=20.0)
            journal=domain.repo.commands;journal.record(op['command_id'],sid,'primary',value)
            unknown=journal.record(op['command_id'],sid,'primary',value|{'status':'unknown','updated_at':10.0})
            assert unknown['phase']=='unknown'
            late=journal.record(op['command_id'],sid,'primary',value)
            assert late['phase']=='unknown'
            terminal=journal.record(op['command_id'],sid,'primary',value|{'status':'succeeded','updated_at':5.0})
            assert terminal['phase']=='succeeded'
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())


def test_explicit_environment_quota_rejection_does_not_become_unknown(tmp_path):
    from naiyue_test_agent.contracts.cloud_tasks import CloudError
    async def scenario():
        manager,domain,store,sid,remote,binding=await prepared(tmp_path)
        try:
            remote.task.update(environment_phase='absent',runner_phase='unconfigured',lifecycle='stopped')
            original=remote.request
            def quota(method,path,**kwargs):
                if method=='POST' and path.endswith('/commands'):raise CloudError('environment_capacity')
                return original(method,path,**kwargs)
            remote.request=quota
            req=dict(command_id=uid(),local_session_id=sid,expected_binding_revision=binding['revision'],text='work',model_id='A',draft_revision=None)
            op=await manager.commands.submit(domain,binding['binding_id'],'message',req);await manager.drain()
            saved=domain.repo.operation(op['command_id'],sid)
            assert saved['phase']=='rejected' and saved['error_code']=='environment_capacity'
            binding=await manager.read(domain,binding['binding_id'],sid)
            new=domain.repo.commands.reserve(binding['binding_id'],sid,'message',req|{'command_id':uid(),'expected_binding_revision':binding['revision']})
            assert new['phase']=='reserved'
            await manager.close()
        finally:store.close()
    asyncio.run(scenario())
