"""Additive DTO defaults do not change the identity of already frozen commands."""
import asyncio,json
from uuid import uuid4
from test_result_receive import receiving
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRecord,DeliveryRequest


def test_legacy_receive_and_delivery_default_intents_still_reconcile(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        try:
            request=dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[])
            receipt=await manager.receives.submit(domain,binding['binding_id'],rid,request);await manager.drain()
            db=store.cloud_receives.database
            with db.transaction(write=True) as c:
                fingerprint=digest(dict(binding_id=binding['binding_id'],result_id=rid,request=request))
                c.execute('UPDATE naiyue_cloud_receives SET request_digest=?,request_json=? WHERE receive_id=?',
                    (fingerprint,json.dumps(request),receipt.receive_id))
            assert (await manager.receives.submit(domain,binding['binding_id'],rid,request)).receive_id==receipt.receive_id
            old=dict(command_id=str(uuid4()),local_session_id=sid,receive_id=receipt.receive_id,action='apply',
                destination_handle=domain.project_id,name='')
            record=DeliveryRecord(command_id=old['command_id'],local_project_id=domain.project_id,local_session_id=sid,
                receive_id=receipt.receive_id,action='apply',phase='prepared')
            with db.transaction(write=True) as c:
                c.execute('INSERT INTO naiyue_cloud_deliveries VALUES(?,?,?,?,?,?)',(record.command_id,sid,domain.project_id,
                    digest(old),record.model_dump_json(),json.dumps(dict(request=old,root_identity={}))))
            assert domain.deliveries.reserve(DeliveryRequest.model_validate(old),record,{},128).command_id==record.command_id
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())
