"""Preparing analysis creates only references and editable instructions, never report-as-user text."""
import asyncio
from uuid import uuid4
from test_result_receive import receiving


def test_analysis_draft_is_stable_and_does_not_write_chat_or_memory(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        try:
            record=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[files[2]['artifact']['artifact_id']]))
            await manager.drain();before=store.sessions.get(sid)
            first=await manager.receives.analysis(domain,record.receive_id,sid);second=await manager.receives.analysis(domain,record.receive_id,sid)
            assert first==second and len(first.material_refs)==3 and first.receive_id==record.receive_id
            assert '第二行' not in first.text and first.material_refs[-1].reported is True
            assert store.sessions.get(sid)==before and str(tmp_path) not in first.model_dump_json()
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())
