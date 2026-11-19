"""Old interactive code operations are retired, but existing receipts stay readable."""
import asyncio
from uuid import uuid4

from test_manual_commands_routes import configured
from test_result_receive import receiving


def test_legacy_code_delivery_writes_retire_but_receipt_stays_readable(monkeypatch,tmp_path):
    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks;store=t.host.sessions._persistence.storage
            state=(t.manager,domain,store,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,_,_,_=await receiving(tmp_path,prepared_state=state)
            try:
                received=await t.manager.receives.submit(domain,t.bid,rid,dict(command_id=str(uuid4()),local_session_id=t.sid))
                await t.manager.drain()
                request=dict(command_id=str(uuid4()),local_session_id=t.sid,receive_id=received.receive_id,
                    action='export',destination_handle=t.query['local_project_id'],name='delivered')
                base=t.base+'/cloud-deliveries'
                denied=await t.client.post(base,params=t.query,headers=t.headers|{'Authorization':''},json=request)
                assert denied.status_code==401
                retired=await t.client.post(base,params=t.query,headers=t.headers,json=request)
                assert retired.status_code==410 and retired.json()['error']['code']=='cloud_result_workflow_retired'
                from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest, DeliveryRecord
                legacy=DeliveryRequest.model_validate(request)
                record=DeliveryRecord(command_id=legacy.command_id,local_session_id=t.sid,local_project_id=domain.project_id,
                                      receive_id=legacy.receive_id,action='export',phase='prepared')
                domain.deliveries.reserve(legacy,record,{},10)
                url=base+'/'+request['command_id']
                preview=await t.client.get(url,params=t.query,headers=t.headers)
                assert preview.json()['phase']=='prepared' and preview.json()['error_code']=='cloud_result_workflow_retired'
                history=await t.client.get(t.base+'/cloud-legacy-deliveries',params=t.query,headers=t.headers)
                assert history.status_code==200 and [entry['command_id'] for entry in history.json()]==[record.command_id]
                domain.deliveries.save(record.model_copy(update={'phase':'partial','done':['test.py']}))
                history=await t.client.get(t.base+'/cloud-legacy-deliveries',params=t.query,headers=t.headers)
                entry,=history.json()
                assert entry['recovery_required'] and entry['done']==['test.py']
                assert entry['evidence_path'].endswith('/deliveries/'+record.command_id)
                cross=await t.client.get(url,params=t.query|{'local_session_id':t.other_sid},headers=t.headers)
                assert cross.status_code==404
                denied=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':False})
                assert denied.status_code==422
                reply=await t.client.post(url+'/execute',params=t.query,headers=t.headers,json={'confirm':True})
                assert reply.status_code==410
                reply=await t.client.post(url+'/recheck',params=t.query,headers=t.headers,json={})
                assert reply.status_code==410
                assert not (tmp_path/'a/delivered').exists() and t.model.seen==[]
            finally:remote.http.close()
    asyncio.run(scenario())


def test_retired_preparing_code_is_not_an_active_delivery(monkeypatch,tmp_path):
    from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest,DeliveryRecord
    from naiyue_test_agent.storage.cloud_delivery import check_deletion

    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks
            request=DeliveryRequest(command_id=str(uuid4()),local_session_id=t.sid,receive_id=str(uuid4()),
                                    action='export',destination_handle=domain.project_id,name='old-export')
            record=DeliveryRecord(command_id=request.command_id,local_project_id=domain.project_id,
                                  local_session_id=t.sid,receive_id=request.receive_id,action='export',
                                  name=request.name,phase='preparing')
            domain.deliveries.reserve(request,record,{},10)
            with domain.repo.database.transaction() as db:
                check_deletion(db,t.sid)
    asyncio.run(scenario())


def test_retired_export_only_protects_original_export_directory(monkeypatch,tmp_path):
    from naiyue_test_agent.application.cloud_local.result_tool import LocalResultTool
    from naiyue_test_agent.cloud_files.delivery_paths import directory_identity
    from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest,DeliveryRecord

    async def scenario():
        async with configured(monkeypatch,tmp_path) as t:
            domain=t.host.cloud_tasks
            state=(t.manager,domain,t.host.sessions._persistence.storage,t.sid,t.adapter,t.binding)
            _,_,_,_,remote,_,rid,files,data,_=await receiving(tmp_path,prepared_state=state)
            try:
                project=tmp_path/'a'
                protected=project/'old-export'
                protected.mkdir()
                outside=project/'reports'
                outside.mkdir()
                req=DeliveryRequest(command_id=str(uuid4()),local_session_id=t.sid,receive_id=str(uuid4()),
                                      action='export',destination_handle=domain.project_id,name=protected.name)
                record=DeliveryRecord(command_id=req.command_id,local_project_id=domain.project_id,
                                      local_session_id=t.sid,receive_id=req.receive_id,action='export',name=req.name,
                                      phase='partial')
                domain.deliveries.reserve(req,record,dict(root_identity=directory_identity(project)),10)
                port=LocalResultTool(t.manager,domain)
                aid=files[2]['artifact']['artifact_id']
                for target,expected in ((protected/'result.txt','failed'),(outside/'result.txt','completed')):
                    cid=str(uuid4())
                    await port.download(t.sid,cid,dict(binding_id=t.bid,result_id=rid,artifact_id=aid,
                                                        path=str(target),force=False))
                    await t.manager.drain()
                    assert (await port.status(t.sid,cid))['state']==expected
                assert not (protected/'result.txt').exists()
                assert (outside/'result.txt').read_bytes()==data[aid]
            finally:
                remote.http.close()
    asyncio.run(scenario())
