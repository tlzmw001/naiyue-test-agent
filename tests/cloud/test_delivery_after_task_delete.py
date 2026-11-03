"""A completed local receive keeps its exact source after cloud command redaction."""
import asyncio,json
from pathlib import Path
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud_local.delivery_inputs import source_for
from test_manual_commands_routes import configured
from test_result_receive import receiving
from test_delivery_branch import git


async def received_git(t,tmp_path):
    root=tmp_path/'a';domain=t.host.cloud_tasks
    git(root,'init','--quiet');git(root,'config','user.name','fixture');git(root,'config','user.email','fixture@example.invalid')
    git(root,'add','test.py');git(root,'commit','--quiet','-m','baseline')
    selected=await domain.capture(t.sid,root,None,t.manager.settings.input_limits,git=True)
    t.manager.connections.client=t.remote
    created=await t.manager.submit(domain,'project',dict(command_id=str(uuid4()),local_session_id=t.sid,
        profile_id='work',source_handle=selected['source_handle'],template_id='test'))
    await t.manager.drain();t.manager.connections.client=t.adapter
    t.remote.task.update(runner_phase='ready',runner_model_ids=['A'],reclaim_blockers=[],last_quiescent_at=1.0)
    binding=await t.manager.read(domain,created['binding']['binding_id'],t.sid)
    state=(t.manager,domain,t.host.sessions._persistence.storage,t.sid,t.adapter,binding)
    _,_,_,_,remote,_,rid,_,data,_=await receiving(tmp_path,prepared_state=state)
    original=remote.request;metadata=original('GET','/tasks/'+binding['task_id']+'/results/'+rid)
    source=domain.repo.source(selected['source_handle'],t.sid)
    from naiyue_test_agent.cloud_files.archive import validate_package
    from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
    from naiyue_test_agent.contracts.cloud_inputs import InputPackage
    from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultBoundary
    inp=Path(source['captured']['directory']);package=InputPackage.model_validate_json((inp/'input.json').read_bytes())
    work=tmp_path/'runner';work.mkdir()
    validate_package(inp/'archive',package.codec,t.manager.settings.input_limits,destination=work,expected_id=package.input_id)
    (work/'test.py').write_bytes(b'cloud change\r\n')
    spool=freeze_result(work,inp,tmp_path/'spool',ResultPlan.model_validate(metadata['plan']),
        ResultBoundary.model_validate(metadata['boundary']),1,t.manager.settings.result_limits,t.manager.settings.input_limits)
    result=json.loads((spool/'result.json').read_bytes());result['phase']='available'
    data.clear();data.update({f['artifact']['artifact_id']:(spool/'files'/f['artifact']['artifact_id']).read_bytes() for f in result['bundle']['files']})
    remote.request=lambda method,path,**kw:result if method=='GET' and path=='/tasks/'+binding['task_id']+'/results/'+rid else original(method,path,**kw)
    record=await t.manager.receives.submit(domain,binding['binding_id'],rid,
        dict(command_id=str(uuid4()),local_session_id=t.sid,artifact_ids=[]))
    await t.manager.drain();record=await t.manager.receives.read(domain,record.receive_id,t.sid)
    assert record.phase=='available'
    return binding,record,selected


def delete_binding(domain,binding,sid):
    request=dict(command_id=str(uuid4()),local_session_id=sid,expected_binding_revision=binding['revision'],
        expected_revision=binding['cloud']['revision'],expected_generation=binding['cloud']['generation'],
        preview_digest='a'*64,acknowledge_unknown=False)
    journal=domain.repo.commands;journal.reserve(binding['binding_id'],sid,'delete',request)
    payload={k:v for k,v in request.items() if k not in ('local_session_id','expected_binding_revision')};payload['confirm_task_id']=binding['task_id']
    fingerprint=digest(dict(task_id=binding['task_id'],request=payload))
    journal.fix(request['command_id'],sid,'primary',payload,'/tasks/'+binding['task_id']+'/delete','task:delete',fingerprint)
    receipt=dict(command_id=request['command_id'],resource_id=binding['task_id'],operation='task:delete',request_digest=fingerprint,
        expected_generation=request['expected_generation'],status='succeeded',http_status=200,created_at=1.0,updated_at=2.0,error_code=None,
        result=dict(phase='finished',tombstone=dict(instance_id=binding['instance_id'],task_id=binding['task_id'],
            generation=request['expected_generation'],delete_command_id=request['command_id'],deleted_at=2.0)))
    journal.record(request['command_id'],sid,'primary',receipt)
    assert domain.repo.operation(binding['create_command_id'],sid)['request']=={'command_id':binding['create_command_id']}
    assert not domain.repo.binding(binding['binding_id'],sid)['retained']


@pytest.mark.parametrize('action',['export','apply','branch'])
def test_completed_receive_delivers_offline_after_task_deletion(monkeypatch,tmp_path,action):
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            binding,record,selected=await received_git(t,tmp_path);domain=t.host.cloud_tasks
            try:
                original_head=git(tmp_path/'a','rev-parse','HEAD')
                delete_binding(domain,binding,t.sid)
                def offline(*args,**kwargs):raise AssertionError('complete local delivery cannot reconnect')
                t.adapter.request=offline
                request=dict(command_id=str(uuid4()),local_session_id=t.sid,receive_id=record.receive_id,
                    action=action,destination_handle=t.query['local_project_id'],name='delivered' if action!='apply' else '')
                path=t.base+'/cloud-deliveries'
                response=await t.client.post(path,params=t.query,headers=t.headers,json=request)
                assert response.status_code==202,response.text
                await t.manager.drain();path+='/'+request['command_id']
                preview=(await t.client.get(path,params=t.query,headers=t.headers)).json()
                assert preview['phase']=='prepared' and preview['conflicts']==[],preview
                assert (tmp_path/'a/test.py').read_text()=='fixture'
                executed=await t.client.post(path+'/execute',params=t.query,headers=t.headers,json={'confirm':True})
                assert executed.status_code==202,executed.text
                await t.manager.drain();final=(await t.client.get(path,params=t.query,headers=t.headers)).json()
                assert final['phase']=='completed',final
                if action=='apply':assert (tmp_path/'a/test.py').read_bytes()==b'cloud change\r\n'
                elif action=='export':assert (tmp_path/'a/delivered/test.py').read_bytes()==b'cloud change\r\n'
                else:assert git(tmp_path/'a','show','delivered:test.py')==b'cloud change\r\n'
                assert git(tmp_path/'a','rev-parse','HEAD')==original_head
                assert record.source_handle==selected['source_handle']
                assert record.model_validate_json(record.model_dump_json()).source_handle==record.source_handle
                assert t.model.seen==[]
            finally:t.adapter.http.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault',['missing','wrong_input','cross_session','wrong_binding','wrong_instance'])
def test_fixed_receive_source_rejects_missing_or_mismatched_identity(monkeypatch,tmp_path,fault):
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;store=t.host.sessions._persistence.storage
            state=(t.manager,domain,store,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,_,_,_=await receiving(tmp_path,prepared_state=state)
            try:
                record=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid,artifact_ids=[]))
                await t.manager.drain();record=await t.manager.receives.read(domain,record.receive_id,t.sid)
                if fault=='missing':bad=record.model_copy(update={'source_handle':None})
                elif fault=='wrong_instance':bad=record.model_copy(update={'instance_id':str(uuid4())})
                elif fault=='wrong_binding':bad=record.model_copy(update={'task_id':uuid4().hex})
                else:
                    sid=await t.host.sessions.create('A') if fault=='cross_session' else t.sid
                    (tmp_path/'a/other.txt').write_text('another input')
                    other=await domain.capture(sid,tmp_path/'a',{'paths':['other.txt']},t.manager.settings.input_limits)
                    bad=record.model_copy(update={'source_handle':other['source_handle']})
                with pytest.raises(CloudError):source_for(domain,bad)
            finally:remote.http.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault',['missing','wrong_input','cross_session','wrong_binding'])
def test_receive_reserves_exact_source_or_rolls_back_atomically(monkeypatch,tmp_path,fault):
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;store=t.host.sessions._persistence.storage
            state=(t.manager,domain,store,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,_,_,_=await receiving(tmp_path,prepared_state=state)
            try:
                op=domain.repo.operation(t.binding['create_command_id'],t.sid)
                if fault=='missing':op['request']={'command_id':op['command_id']}
                elif fault=='wrong_binding':op['binding_id']=str(uuid4())
                else:
                    sid=await t.host.sessions.create('A') if fault=='cross_session' else t.sid
                    (tmp_path/'a/other.txt').write_text('other source')
                    selected=await domain.capture(sid,tmp_path/'a',{'paths':['other.txt']},t.manager.settings.input_limits)
                    op['request']['source_handle']=selected['source_handle']
                with store.database.transaction(write=True,error_code='fixture') as db:
                    db.execute('UPDATE naiyue_cloud_operations SET body_json=? WHERE command_id=?',(json.dumps(op),op['command_id']))
                cid=str(uuid4())
                with pytest.raises(CloudError,match='cloud_receive_(identity|input)_mismatch'):
                    await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=cid,local_session_id=t.sid,artifact_ids=[]))
                with pytest.raises(CloudError,match='cloud_receive_not_found'):domain.materials.repo.read(cid,t.sid)
                assert not t.manager.receives.flights
            finally:remote.http.close()
    asyncio.run(run())
