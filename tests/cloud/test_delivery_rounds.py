"""Real non-Git multi-round return, conflicts, immutable input and receipt recovery."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4
from test_manual_commands_routes import configured
from test_result_receive import receiving
from naiyue_test_agent.cloud_files.archive import validate_package
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultBoundary
from naiyue_test_agent.contracts.cloud_inputs import InputPackage


def test_non_git_rounds_advance_only_after_atomic_completion(monkeypatch,tmp_path):
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;state=(t.manager,domain,t.host.sessions._persistence.storage,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,first_id,_,data,_=await receiving(tmp_path,prepared_state=state)
            try:
                original=remote.request;metadata=original('GET','/tasks/'+t.binding['task_id']+'/results/'+first_id)
                source=domain.repo.source(domain.repo.operation(t.binding['create_command_id'],t.sid)['request']['source_handle'],t.sid)
                inp=Path(source['captured']['directory']);package=InputPackage.model_validate_json((inp/'input.json').read_bytes())
                original_hash=package.input_id;runner=tmp_path/'runner';runner.mkdir()
                validate_package(inp/'archive',package.codec,t.manager.settings.input_limits,destination=runner,expected_id=package.input_id)
                results={};base=t.base+'/cloud-deliveries'
                def owner(method,path,**kw):
                    if method=='GET' and path.rsplit('/',1)[-1] in results:return results[path.rsplit('/',1)[-1]]
                    if method=='POST' and path.endswith(('/lease','/release')):
                        return dict(result_id=path.split('/')[-2],lease_id=kw['json']['lease_id'],expires_at=9999999999,released=path.endswith('/release'))
                    return original(method,path,**kw)
                remote.request=owner
                async def prepare():
                    rid=str(uuid4());plan=metadata['plan']|dict(result_id=rid,source_run_id=uuid4().hex,source_command_id=str(uuid4()))
                    spool=freeze_result(runner,inp,tmp_path/('spool-'+rid),ResultPlan.model_validate(plan),
                        ResultBoundary.model_validate(metadata['boundary']),1,t.manager.settings.result_limits,t.manager.settings.input_limits)
                    value=json.loads((spool/'result.json').read_text());value['phase']='available';results[rid]=value
                    data.update({f['artifact']['artifact_id']:(spool/'files'/f['artifact']['artifact_id']).read_bytes() for f in value['bundle']['files']})
                    receive=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid,artifact_ids=[]))
                    await t.manager.drain();cid=str(uuid4())
                    response=await t.client.post(base,params=t.query,headers=t.headers,json=dict(command_id=cid,local_session_id=t.sid,
                        receive_id=receive.receive_id,action='apply',destination_handle=t.query['local_project_id']))
                    assert response.status_code==202,response.text
                    await t.manager.drain();url=base+'/'+cid
                    return rid,cid,url,(await t.client.get(url,params=t.query,headers=t.headers)).json()
                async def execute(url,force=False):
                    response=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json=dict(confirm=True,force=force))
                    assert response.status_code==202,response.text
                    await t.manager.drain();return (await t.client.get(url,params=t.query,headers=t.headers)).json()
                (runner/'test.py').write_text('first');(runner/'round1.py').write_text('added')
                r1,c1,u1,p1=await prepare()
                assert p1['phase']=='prepared' and p1['conflicts']==[] and p1['base_result_id'] is None
                assert (await execute(u1))['phase']=='completed'
                # O2 reverses one change to I and removes the previously added path.
                (runner/'test.py').write_text('fixture');(runner/'round1.py').unlink();(runner/'round2.py').write_text('second')
                r2,c2,u2,p2=await prepare()
                assert p2['conflicts']==[] and p2['base_result_id']==r1
                assert {(c['path'],c['operation']) for c in p2['changes']}=={('test.py','modify'),('round1.py','delete'),('round2.py','add')}
                # A local edit after preflight cannot be overwritten by prior confirmation.
                (tmp_path/'a/test.py').write_text('concurrent edit');(tmp_path/'a/unrelated').write_text('keep')
                blocked=await execute(u2)
                assert blocked['phase']=='partial' and blocked['error_code']=='cloud_delivery_preflight_changed'
                assert (tmp_path/'a/test.py').read_text()=='concurrent edit'
                reply=await t.client.post(u2+'/recheck',params=t.query,headers=t.headers,json={});assert reply.status_code==202
                await t.manager.drain();review=(await t.client.get(u2,params=t.query,headers=t.headers)).json()
                assert review['force_allowed'] and review['conflicts']==['test.py']
                complete=domain.deliveries.complete;failed=False
                def lost_receipt(record,meta):
                    nonlocal failed
                    if record.command_id==c2 and not failed:failed=True;raise OSError('controlled completion storage failure')
                    return complete(record,meta)
                monkeypatch.setattr(domain.deliveries,'complete',lost_receipt)
                partial=await execute(u2,True);assert partial['phase']=='partial'
                _,meta=domain.deliveries.read(c2,t.sid)
                assert domain.deliveries.baseline(meta['baseline_key'])[0]==c1
                from naiyue_test_agent.contracts.cloud_delivery import DeliveryRecord
                domain.deliveries.save(DeliveryRecord.model_validate(partial).model_copy(update={'phase':'unknown'}))
                preserved=await t.manager.deliveries.prepare(domain,meta['request'])
                assert preserved.phase=='unknown'
                await t.manager.drain()
                assert (tmp_path/'a/test.py').read_text()=='fixture' and not (tmp_path/'a/round1.py').exists()
                receive_id=meta['request']['receive_id']
                receipt=domain.materials.repo.read(receive_id,t.sid)
                patch=next(f for f in receipt.files if f.role=='patch')
                domain.materials.files.blob(patch.artifact.sha256).unlink()
                unavailable=await execute(u2,True)
                assert unavailable['phase']=='partial' and unavailable['error_code']=='cloud_material_unavailable'
                await t.manager.receives.retry(domain,receive_id,t.sid,refresh=True);await t.manager.drain()
                assert (await execute(u2,True))['phase']=='completed'
                assert domain.deliveries.baseline(meta['baseline_key'])[0]==c2
                assert (tmp_path/'a/unrelated').read_text()=='keep'
                assert package.input_id==original_hash and not (tmp_path/'a/.git').exists()
                _,_,_,p3=await prepare();assert p3['changes']==[] and p3['base_result_id']==r2
            finally:remote.http.close()
    asyncio.run(run())
