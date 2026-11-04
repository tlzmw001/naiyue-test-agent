"""Report destination selection and collision approval use the real delivery routes."""
import asyncio
from uuid import uuid4
from test_manual_commands_routes import configured
from test_result_receive import receiving
from naiyue_test_agent.application.projects.locations import directory_facts,directory_identity


def test_reports_saved_to_existing_or_new_directory_with_explicit_overwrite(monkeypatch,tmp_path):
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;state=(t.manager,domain,t.host.sessions._persistence.storage,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,files,data,_=await receiving(tmp_path,prepared_state=state)
            try:
                received=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid,
                    artifact_ids=[files[2]['artifact']['artifact_id']],include_code=False))
                await t.manager.drain()
                destination=tmp_path/'reports';destination.mkdir();(destination/'report.txt').write_text('existing user report')
                (destination/'untouched').write_text('keep')
                base=t.base+'/cloud-deliveries'
                async def prepare(name=''):
                    request=dict(command_id=str(uuid4()),local_session_id=t.sid,receive_id=received.receive_id,action='files',
                        destination_handle=t.query['local_project_id'],destination_path=str(destination),
                        expected_directory=directory_identity(destination,directory_facts(destination)),name=name)
                    result=await t.client.post(base,params=t.query,headers=t.headers,json=request)
                    assert result.status_code==202,result.text
                    await t.manager.drain();url=base+'/'+request['command_id']
                    return url,(await t.client.get(url,params=t.query,headers=t.headers)).json()
                url,preview=await prepare()
                assert preview['phase']=='prepared' and preview['conflicts']==['report.txt'] and preview['force_allowed']
                denied=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True})
                assert denied.status_code==409 and (destination/'report.txt').read_text()=='existing user report'
                (destination/'report.txt').write_text('later edit')
                changed=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True,'force':True})
                assert changed.status_code==202
                await t.manager.drain();assert (destination/'report.txt').read_text()=='later edit'
                recheck=await t.client.post(url+'/recheck',params=t.query,headers=t.headers,json={})
                assert recheck.status_code==202,recheck.text
                await t.manager.drain()
                await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True,'force':True});await t.manager.drain()
                assert (await t.client.get(url,params=t.query,headers=t.headers)).json()['phase']=='completed'
                assert (destination/'report.txt').read_bytes()==data[files[2]['artifact']['artifact_id']]
                assert (destination/'untouched').read_text()=='keep'
                url,preview=await prepare('new-folder')
                assert preview['phase']=='prepared' and not preview['conflicts']
                await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True});await t.manager.drain()
                assert (destination/'new-folder/report.txt').read_bytes()==data[files[2]['artifact']['artifact_id']]
                assert t.model.seen==[]
            finally:remote.http.close()
    asyncio.run(run())


def test_report_cache_change_during_copy_never_reaches_the_selected_folder(monkeypatch,tmp_path):
    from naiyue_test_agent.application.cloud_local import delivery_files
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;state=(t.manager,domain,t.host.sessions._persistence.storage,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,files,_,_=await receiving(tmp_path,prepared_state=state)
            try:
                received=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid,
                    artifact_ids=[files[2]['artifact']['artifact_id']],include_code=False))
                await t.manager.drain();destination=tmp_path/'reports';destination.mkdir();(destination/'report.txt').write_text('local report')
                cid=str(uuid4());base=t.base+'/cloud-deliveries'
                request=dict(command_id=cid,local_session_id=t.sid,receive_id=received.receive_id,action='files',
                    destination_handle=t.query['local_project_id'],destination_path=str(destination),
                    expected_directory=directory_identity(destination,directory_facts(destination)))
                assert (await t.client.post(base,params=t.query,headers=t.headers,json=request)).status_code==202
                await t.manager.drain();url=base+'/'+cid
                copy=delivery_files.shutil.copyfileobj;injected=[]
                def changed(source,target,**kw):
                    if not injected:
                        injected.append(True);domain.materials.files.blob(files[2]['artifact']['sha256']).write_bytes(b'corrupt during copying')
                    return copy(source,target,**kw)
                monkeypatch.setattr(delivery_files.shutil,'copyfileobj',changed)
                reply=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True,'force':True})
                assert reply.status_code==202;await t.manager.drain()
                assert injected and (destination/'report.txt').read_text()=='local report'
                final=(await t.client.get(url,params=t.query,headers=t.headers)).json()
                assert final['phase']=='partial' and final['error_code']=='cloud_material_unavailable'
            finally:remote.http.close()
    asyncio.run(run())
