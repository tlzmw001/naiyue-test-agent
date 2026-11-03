"""Real authenticated client routes separate receiving, preflight and confirmed writes."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4
from test_manual_commands_routes import configured
from test_result_receive import receiving


def test_export_requires_confirmation_and_keeps_owner_scope(monkeypatch,tmp_path):
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;store=t.host.sessions._persistence.storage
            state=(t.manager,domain,store,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,binding,rid,files,_,_=await receiving(tmp_path,prepared_state=state)
            try:
                record=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid,artifact_ids=[]))
                await t.manager.drain()
                request=dict(command_id=str(uuid4()),local_session_id=t.sid,receive_id=record.receive_id,
                    action='export',destination_handle=t.query['local_project_id'],name='delivered')
                path=t.base+'/cloud-deliveries'
                denied=await t.client.post(path,params=t.query,headers=t.headers|{'Authorization':''},json=request)
                assert denied.status_code==401
                created=await t.client.post(path,params=t.query,headers=t.headers,json=request)
                assert created.status_code==202,created.text
                await t.manager.drain()
                url=path+'/'+request['command_id']
                preview=await t.client.get(url,params=t.query,headers=t.headers)
                assert preview.json()['phase']=='prepared',preview.text
                assert not (tmp_path/'a/delivered').exists()
                cross=await t.client.get(url,params=t.query|{'local_session_id':t.other_sid},headers=t.headers)
                assert cross.status_code==404
                unconfirmed=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':False})
                assert unconfirmed.status_code==422 and not (tmp_path/'a/delivered').exists()
                executed=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True})
                assert executed.status_code==202,executed.text
                await t.manager.drain()
                final=await t.client.get(url,params=t.query,headers=t.headers)
                assert final.json()['phase']=='completed',final.text
                assert (tmp_path/'a/delivered/test.py').read_text()=='fixture'
                again=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True})
                assert again.json()['phase']=='completed'
                assert t.model.seen==[] and (await t.host.sessions.status(t.sid))['run_id'] is None
            finally:remote.http.close()
    asyncio.run(scenario())


def test_actual_changed_patch_applies_only_after_preflight_and_explicit_confirmation(monkeypatch,tmp_path):
    from naiyue_test_agent.cloud_files.archive import validate_package
    from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
    from naiyue_test_agent.contracts.cloud_results import ResultPlan,ResultBoundary
    from naiyue_test_agent.contracts.cloud_inputs import InputPackage
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;store=t.host.sessions._persistence.storage
            state=(t.manager,domain,store,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,_,data,_=await receiving(tmp_path,prepared_state=state)
            try:
                original=remote.request;metadata=original('GET','/tasks/'+t.binding['task_id']+'/results/'+rid)
                source=domain.repo.source(domain.repo.operation(t.binding['create_command_id'],t.sid)['request']['source_handle'],t.sid)
                inp=Path(source['captured']['directory']);package=InputPackage.model_validate_json((inp/'input.json').read_bytes())
                runner=tmp_path/'runner';runner.mkdir()
                validate_package(inp/'archive',package.codec,t.manager.settings.input_limits,destination=runner,expected_id=package.input_id)
                (runner/'test.py').write_bytes(b'cloud code\r\n');(runner/'new.proto').write_text('syntax = "proto3";')
                spool=freeze_result(runner,inp,tmp_path/'spool',ResultPlan.model_validate(metadata['plan']),
                    ResultBoundary.model_validate(metadata['boundary']),1,t.manager.settings.result_limits,t.manager.settings.input_limits)
                result=json.loads((spool/'result.json').read_text())
                result['phase']='available'  # Controlled owner publishes the immutable Runner spool.
                data.clear();data.update({f['artifact']['artifact_id']:(spool/'files'/f['artifact']['artifact_id']).read_bytes() for f in result['bundle']['files']})
                remote.request=lambda method,path,**kw:result if method=='GET' and path=='/tasks/'+t.binding['task_id']+'/results/'+rid else original(method,path,**kw)
                received=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid,artifact_ids=[]))
                await t.manager.drain();(tmp_path/'a/unrelated').write_text('local dirty')
                request=dict(command_id=str(uuid4()),local_session_id=t.sid,receive_id=received.receive_id,
                    action='apply',destination_handle=t.query['local_project_id'])
                url=t.base+'/cloud-deliveries'
                response=await t.client.post(url,params=t.query,headers=t.headers,json=request)
                assert response.status_code==202,response.text
                await t.manager.drain();url+='/'+request['command_id']
                ready=(await t.client.get(url,params=t.query,headers=t.headers)).json()
                assert ready['phase']=='prepared' and ready['conflicts']==[],ready
                assert {c['path'] for c in ready['changes']}=={'test.py','new.proto'}
                assert (tmp_path/'a/test.py').read_text()=='fixture' and not (tmp_path/'a/new.proto').exists()
                response=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True})
                assert response.status_code==202,response.text
                await t.manager.drain()
                final=(await t.client.get(url,params=t.query,headers=t.headers)).json()
                assert final['phase']=='completed',final
                assert (tmp_path/'a/test.py').read_bytes()==b'cloud code\r\n'
                assert (tmp_path/'a/new.proto').read_text()=='syntax = "proto3";'
                assert (tmp_path/'a/unrelated').read_text()=='local dirty' and t.model.seen==[]
            finally:remote.http.close()
    asyncio.run(scenario())
