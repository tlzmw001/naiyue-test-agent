"""Local receipts own selected immutable bytes; reads never resume a transfer."""
import asyncio,hashlib
from pathlib import Path
from uuid import uuid4
import httpx,pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_receive import ReceiveLimits
from naiyue_test_agent.contracts.cloud_inputs import InputPackage
from naiyue_test_agent.contracts.cloud_results import OutputManifest
from test_manual_task_commands import prepared


async def receiving(tmp_path,*,fail_segment=None,prepared_state=None):
    manager,domain,store,sid,remote,binding=prepared_state or await prepared(tmp_path)
    manager.settings=manager.settings.model_copy(update={'receive_limits':ReceiveLimits(segment_bytes=4)})
    source=store.cloud_tasks.source(store.cloud_tasks.operation(binding['create_command_id'],sid)['request']['source_handle'],sid)
    package=InputPackage.model_validate_json((Path(source['captured']['directory'])/'input.json').read_bytes())
    rid=str(uuid4());run=uuid4().hex;epoch=str(uuid4());captured=1.0
    plan=dict(result_id=rid,source_command_id=str(uuid4()),source_run_id=run,task_id=binding['task_id'],project_id=binding['cloud_project_id'],input_id=binding['input_id'],source_generation=1,output_directory='.nyt-output')
    boundary=dict(kind='run',epoch=epoch,seq=1,outcome='failed')
    manifest=OutputManifest(input_id=package.input_id,policy_sha256=package.manifest.selection['policy_sha256'],entries=[e.model_dump() for e in package.manifest.entries]).canonical
    files=[];data={}
    for role,name,body in [('manifest','output-manifest.json',manifest),('patch','changes.patch',b''),('artifact','report.txt','first\n第二行\nthird\n'.encode())]:
        fid=str(uuid4());data[fid]=body
        files.append(dict(role=role,artifact=dict(command_id=rid,artifact_id=fid,relative_name=name,task_id=binding['task_id'],project_id=binding['cloud_project_id'],input_id=binding['input_id'],generation=1,captured_at=captured,size=len(body),sha256=hashlib.sha256(body).hexdigest())))
    result=dict(plan=plan,phase='available',boundary=boundary,bundle=dict(plan=plan,boundary=boundary,generation=1,captured_at=captured,files=files))
    original=remote.request;requests=[];failed=False
    remote.lease_events=[]
    def request(method,path,**kw):
        if method=='POST' and path in ('/tasks/'+binding['task_id']+'/results/'+rid+'/lease','/tasks/'+binding['task_id']+'/results/'+rid+'/release'):
            action=path.rsplit('/',1)[1];lease=kw['json']['lease_id'];remote.lease_events.append((action,lease))
            return dict(result_id=rid,lease_id=lease,expires_at=__import__('time').time()+300,released=action=='release')
        if method=='GET'  and path=='/tasks/'+binding['task_id']+'/results/'+rid:return result
        if method=='GET' and path=='/inputs/'+package.input_id:return package.model_dump(mode='json',by_alias=True)
        return original(method,path,**kw)
    def forward(req):
        nonlocal failed
        fid=req.url.path.split('/')[-2];body=data[fid];header=req.headers.get('range');requests.append((fid,header))
        assert req.headers.get('if-range')=='"'+hashlib.sha256(body).hexdigest()+'"'
        if fail_segment is not None and len(requests)==fail_segment and not failed:
            failed=True;raise httpx.ReadError('controlled segment loss')
        start,end=map(int,header.removeprefix('bytes=').split('-'))
        return httpx.Response(206,content=body[start:end+1],headers={'content-length':str(end-start+1),'content-range':f'bytes {start}-{end}/{len(body)}','etag':'"'+hashlib.sha256(body).hexdigest()+'"','content-type':'application/octet-stream'})
    remote.request=request;remote.http=httpx.Client(transport=httpx.MockTransport(forward),base_url='http://127.0.0.1:32101')
    return manager,domain,store,sid,remote,binding,rid,files,data,requests


def test_receive_resumes_only_explicitly_and_publishes_required_files(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path,fail_segment=2)
        try:
            value=dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[files[2]['artifact']['artifact_id']])
            first=await manager.receives.submit(domain,binding['binding_id'],rid,value);await manager.drain()
            stopped=await manager.receives.read(domain,first.receive_id,sid)
            assert stopped.phase=='failed' and not stopped.active and stopped.files[0].offset==4
            count=len(requests);assert (await manager.receives.read(domain,first.receive_id,sid))==stopped
            assert len(requests)==count
            await manager.receives.retry(domain,first.receive_id,sid);await manager.drain()
            ready=await manager.receives.read(domain,first.receive_id,sid)
            assert ready.phase=='available' and len(ready.files)==3 and all(f.verified for f in ready.files)
            assert requests[2][1]=='bytes=4-7'
            assert ready.input_package.input_id==binding['input_id'] and ready.result.boundary.outcome=='failed'
            complete_count=len(requests)
            again=await manager.receives.submit(domain,binding['binding_id'],rid,value);await manager.drain()
            assert again.receive_id==ready.receive_id and len(requests)==complete_count
            assert str(tmp_path) not in ready.model_dump_json()
            assert store.sessions.get(sid)['run_id'] is None
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_receive_rejects_changed_command_selection(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        try:
            value=dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[])
            first=await manager.receives.submit(domain,binding['binding_id'],rid,value);await manager.drain()
            with pytest.raises(CloudError,match='cloud_receive_conflict'):
                await manager.receives.submit(domain,binding['binding_id'],rid,value|{'artifact_ids':[files[2]['artifact']['artifact_id']]})
            assert (await manager.receives.read(domain,first.receive_id,sid)).phase=='available'
            assert not domain.materials.has_materials('other-session')
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('lost_prefix',['missing','truncated'])
def test_explicit_retry_restarts_missing_unverified_prefix(tmp_path,lost_prefix):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path,fail_segment=2)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();stopped=await manager.receives.read(domain,first.receive_id,sid)
            item=stopped.files[0]
            assert stopped.phase=='failed' and item.offset==4 and not item.verified
            stage=domain.materials.files.stage(first.receive_id,item.artifact.artifact_id)
            if lost_prefix=='missing':stage.unlink()
            else:stage.write_bytes(b'x')
            count=len(requests)
            assert (await manager.receives.read(domain,first.receive_id,sid)).files[0].offset==4
            assert len(requests)==count
            await manager.receives.retry(domain,first.receive_id,sid);await manager.drain()
            ready=await manager.receives.read(domain,first.receive_id,sid)
            assert ready.phase=='available' and ready.receive_id==first.receive_id
            assert requests[count][1]=='bytes=0-3'
            assert ready.result==stopped.result and ready.input_package==stopped.input_package
            for received in ready.files:
                artifact=received.artifact
                assert received.verified
                assert domain.materials.files.blob(artifact.sha256).read_bytes()==data[artifact.artifact_id]
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_first_offset_is_saved_after_staging_directory_sync(tmp_path,monkeypatch):
    from naiyue_test_agent.application.cloud_local import receive_transfer
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path,fail_segment=2)
        synced=[];original_sync=receive_transfer.sync_store_directory;original_save=domain.materials.repo.save
        def sync(path):original_sync(path);synced.append(path)
        def save(record):
            if record.files and record.files[0].offset>0:
                stage=domain.materials.files.stage(record.receive_id,record.files[0].artifact.artifact_id)
                assert stage.parent in synced,'confirmed offset precedes durable staging directory entry'
            return original_save(record)
        monkeypatch.setattr(receive_transfer,'sync_store_directory',sync)
        monkeypatch.setattr(domain.materials.repo,'save',save)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain()
            assert (await manager.receives.read(domain,first.receive_id,sid)).files[0].offset==4
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_metadata_failure_retries_original_selection(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        original=remote.request;once=True
        def missing(method,path,**kw):
            nonlocal once
            if '/results/' in path and once:once=False;raise CloudError('cloud_connection_failed',503)
            return original(method,path,**kw)
        remote.request=missing
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[files[2]['artifact']['artifact_id']]))
            await manager.drain();assert (await manager.receives.read(domain,first.receive_id,sid)).result is None
            await manager.receives.retry(domain,first.receive_id,sid);await manager.drain()
            ready=await manager.receives.read(domain,first.receive_id,sid)
            assert ready.phase=='available' and len(ready.files)==3
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_same_hash_publications_keep_the_first_blob_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace
    from naiyue_test_agent.application.cloud_local.receive_files import ReceiveFiles
    files=ReceiveFiles(SimpleNamespace(path=tmp_path/'sessions.sqlite'));gate=Barrier(2)
    artifact=SimpleNamespace(size=0,sha256=hashlib.sha256(b'').hexdigest());stages=[]
    for _ in range(2):
        path=files.stage(str(uuid4()),str(uuid4()));path.write_bytes(b'');path.chmod(0o600);stages.append(path)
    def publish(path):gate.wait(timeout=5);return files.publish(path,artifact)
    with ThreadPoolExecutor(max_workers=2) as workers:results=list(workers.map(publish,stages))
    assert results[0]==results[1]==files.inspect(files.blob(artifact.sha256),artifact.size,artifact.sha256)
    assert all(not stage.exists() for stage in stages)


def test_published_receipt_ack_loss_does_not_demote_available(tmp_path,monkeypatch):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        original=domain.materials.repo.publish
        def committed(*args):original(*args);raise OSError('controlled committed ACK loss')
        monkeypatch.setattr(domain.materials.repo,'publish',committed)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();ready=await manager.receives.read(domain,first.receive_id,sid)
            assert ready.phase=='available' and domain.materials.has_materials(sid)
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_actual_orphaned_cache_bytes_are_still_charged(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        manager.settings=manager.settings.model_copy(update={'receive_limits':ReceiveLimits(max_cache_bytes=100,segment_bytes=4)})
        orphan=domain.materials.files.blob('b'*64);orphan.write_bytes(b'x'*101);orphan.chmod(0o600)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();record=await manager.receives.read(domain,first.receive_id,sid)
            assert record.phase=='failed' and record.error_code=='cloud_receive_capacity' and not requests
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_reopen_resumes_original_offset_without_metadata_get(tmp_path):
    from contextlib import AsyncExitStack
    from naiyue_test_agent.application.cloud_local.tasks import ManualCloudTasks,LocalTaskDomain
    from naiyue_test_agent.application.lifetime import ApplicationLifetime
    from naiyue_test_agent.storage.project_store import ProjectStore
    from test_manual_task_preparation import Connections
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path,fail_segment=2)
        first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
        await manager.drain();path=store.path;project=domain.project_id;book=manager.book;settings=manager.settings
        await manager.close();store.close()
        reopened=ProjectStore(path);owner=ApplicationLifetime(None,None,AsyncExitStack())
        local=LocalTaskDomain(owner,reopened,project);manager=ManualCloudTasks(settings,book=book,connections=Connections(remote))
        original=remote.request
        def no_metadata(method,path,**kwargs):
            if method=='GET':raise AssertionError('original fixed metadata already persisted')
            return original(method,path,**kwargs)
        remote.request=no_metadata
        try:
            assert (await manager.receives.read(local,first.receive_id,sid)).files[0].offset==4
            assert not manager.receives.flights
            await manager.receives.retry(local,first.receive_id,sid);await manager.drain()
            assert (await manager.receives.read(local,first.receive_id,sid)).phase=='available'
            assert requests[2][1]=='bytes=4-7'
        finally:await manager.close();remote.http.close();reopened.close()
    asyncio.run(run())


def test_corrupt_bytes_with_matching_headers_never_become_materials(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        def corrupt(req):
            fid=req.url.path.split('/')[-2];body=data[fid];start,end=map(int,req.headers['range'].removeprefix('bytes=').split('-'))
            return httpx.Response(206,content=b'x'*(end-start+1),headers={'content-length':str(end-start+1),'content-range':f'bytes {start}-{end}/{len(body)}','etag':'"'+hashlib.sha256(body).hexdigest()+'"'})
        remote.http.close();remote.http=httpx.Client(transport=httpx.MockTransport(corrupt),base_url='http://127.0.0.1:32101')
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();record=await manager.receives.read(domain,first.receive_id,sid)
            assert record.phase=='failed' and not domain.materials.has_materials(sid)
            with pytest.raises(CloudError):await manager.receives.analysis(domain,first.receive_id,sid)
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_http_receive_state_projection_analysis_and_offline_material(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from test_manual_commands_routes import configured
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            prepared_state=(t.manager,t.host.cloud_tasks,SimpleNamespace(cloud_tasks=t.host.cloud_tasks.repo),t.sid,t.adapter,t.binding)
            manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path,prepared_state=prepared_state)
            base=t.base+'/cloud-tasks/'+t.bid+'/results/'+rid+'/receive';body=dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[files[2]['artifact']['artifact_id']])
            try:
                denied=await t.client.post(base,params=t.query,headers=t.headers|{'Authorization':''},json=body)
                assert denied.status_code==401
                response=await t.client.post(base,params=t.query,headers=t.headers,json=body)
                assert response.status_code==202,response.text
                await manager.drain();path=t.base+'/cloud-receives/'+body['command_id']
                status=await t.client.get(path,params=t.query,headers=t.headers)
                assert status.status_code==200 and status.json()['phase']=='available'
                assert status.json()['input_package'] is None and status.json()['result'] is None
                assert (await t.client.get(path,params=t.query|{'local_session_id':t.other_sid},headers=t.headers)).status_code==404
                draft=await t.client.post(path+'/analysis-draft',params=t.query,headers=t.headers,json={})
                assert draft.status_code==200,draft.text
                ref=next(v for v in draft.json()['material_refs'] if v['role']=='artifact')
                remote.request=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('unexpected owner access'))
                read=await t.client.get(t.base+'/cloud-materials/'+ref['material_id']+'/read',params=t.query|{'offset':2,'limit':1},headers=t.headers)
                assert read.status_code==200 and read.json()['text']=='第二行\n'
            finally:remote.http.close()
    asyncio.run(run())


def test_local_receive_renews_owner_lease_before_ranges_and_releases_on_failure(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path,fail_segment=2)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain()
            assert (await manager.receives.read(domain,first.receive_id,sid)).phase=='failed'
            assert remote.lease_events[0]==('lease',first.receive_id)
            assert remote.lease_events[-1]==('release',first.receive_id)
            assert sum(action=='lease' for action,_ in remote.lease_events)>=3
            remote.lease_events.clear()
            await manager.receives.retry(domain,first.receive_id,sid);await manager.drain()
            assert (await manager.receives.read(domain,first.receive_id,sid)).phase=='available'
            assert remote.lease_events[0]==('lease',first.receive_id)
            assert remote.lease_events[-1]==('release',first.receive_id)
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_retry_finishes_fully_downloaded_local_material_without_remote_lease(tmp_path,monkeypatch):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        original=manager.receives._publish;once=True
        def publish(*args):
            nonlocal once
            if once:once=False;raise CloudError('cloud_receive_failed')
            return original(*args)
        monkeypatch.setattr(manager.receives,'_publish',publish)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();failed=await manager.receives.read(domain,first.receive_id,sid)
            assert failed.phase=='failed' and all(f.verified for f in failed.files)
            before=len(requests)
            def offline(*args,**kwargs):raise CloudError('result_not_available')
            remote.request=offline
            await manager.receives.retry(domain,first.receive_id,sid);await manager.drain()
            assert (await manager.receives.read(domain,first.receive_id,sid)).phase=='available'
            assert len(requests)==before
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_report_only_receive_omits_code_transport(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),
                local_session_id=sid,artifact_ids=[files[2]['artifact']['artifact_id']],include_code=False))
            await manager.drain();ready=await manager.receives.read(domain,first.receive_id,sid)
            assert ready.phase=='available' and [f.role for f in ready.files]==['artifact']
            assert {fid for fid,_ in requests}=={files[2]['artifact']['artifact_id']}
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_empty_task_receives_selected_files_and_prepares_analysis_without_source(tmp_path):
    from test_manual_task_preparation import setup
    async def scenario():
        manager,domain,store,project,sid,remote=setup(tmp_path)
        try:
            created=await manager.submit(domain,'project',dict(command_id=str(uuid4()),local_session_id=sid,profile_id='work',template_id='test'))
            await manager.drain();binding=store.cloud_tasks.binding(created['binding']['binding_id'],sid)
            rid=str(uuid4());fid=str(uuid4());data=b'plain output';run=uuid4().hex
            plan=dict(result_id=rid,source_command_id=str(uuid4()),source_run_id=run,task_id=binding['task_id'],project_id=binding['cloud_project_id'],input_id=None,source_generation=1,output_directory='.nyt-output')
            boundary=dict(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
            artifact=dict(command_id=rid,artifact_id=fid,relative_name='.env',task_id=binding['task_id'],project_id=binding['cloud_project_id'],input_id=None,generation=1,captured_at=1.0,size=len(data),sha256=hashlib.sha256(data).hexdigest())
            result=dict(plan=plan,phase='available',boundary=boundary,bundle=dict(plan=plan,boundary=boundary,generation=1,captured_at=1.0,files=[dict(role='artifact',artifact=artifact)]))
            original=remote.request
            def request(method,path,**kw):
                assert not path.startswith('/inputs/')
                if path.endswith('/lease') or path.endswith('/release'):return dict(result_id=rid,lease_id=kw['json']['lease_id'],expires_at=__import__('time').time()+300,released=path.endswith('/release'))
                if path.endswith('/results/'+rid):return result
                return original(method,path,**kw)
            remote.request=request
            def forward(req):
                start,end=map(int,req.headers['range'][6:].split('-'))
                return httpx.Response(206,content=data[start:end+1],headers={'content-length':str(end-start+1),'content-range':f'bytes {start}-{end}/{len(data)}','etag':'"'+artifact['sha256']+'"'})
            remote.http=httpx.Client(transport=httpx.MockTransport(forward),base_url='http://127.0.0.1:32101')
            rec=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,include_code=False,artifact_ids=[fid]))
            await manager.drain();rec=await manager.receives.read(domain,rec.receive_id,sid)
            assert rec.phase=='available' and rec.input_package is None and rec.source_handle is None
            draft=await manager.receives.analysis(domain,rec.receive_id,sid)
            assert len(draft.material_refs)==1 and draft.material_refs[0].input_id is None
            assert domain.materials.files.blob(artifact['sha256']).read_bytes()==data
            from naiyue_test_agent.application.projects.locations import directory_facts,directory_identity
            domain.authority=object()  # Files delivery authorizes the observed destination, not an execution root.
            destination=tmp_path/'saved';destination.mkdir();(destination/'.env').write_text('local')
            command=str(uuid4())
            await manager.deliveries.prepare(domain,dict(command_id=command,local_session_id=sid,receive_id=rec.receive_id,
                action='files',destination_handle=domain.project_id,destination_path=str(destination),
                expected_directory=directory_identity(destination,directory_facts(destination))))
            await manager.drain();delivery=await manager.deliveries.read(domain,command,sid)
            assert delivery.phase=='prepared' and delivery.conflicts==['.env']
            with pytest.raises(CloudError):await manager.deliveries.execute(domain,command,sid)
            await manager.deliveries.execute(domain,command,sid,force=True);await manager.drain()
            assert (await manager.deliveries.read(domain,command,sid)).phase=='completed'
            assert (destination/'.env').read_bytes()==data

        finally:await manager.close();store.close()
    asyncio.run(scenario())
