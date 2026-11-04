"""Only explicit refresh repairs private bytes and rebinds the original materials."""
import asyncio
from uuid import uuid4
import pytest
from test_result_receive import receiving
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_refresh_preserves_receive_material_ids_and_verifies_bytes_again(tmp_path,damage):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        try:
            record=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,
                artifact_ids=[files[2]['artifact']['artifact_id']]))
            await manager.drain();record=await manager.receives.read(domain,record.receive_id,sid)
            report=next(f for f in record.files if f.role=='artifact');path=domain.materials.files.blob(report.artifact.sha256)
            if damage=='missing':path.unlink()
            else:path.write_bytes(b'corrupt private cache')
            before=len(requests)
            assert (await manager.receives.read(domain,record.receive_id,sid)).phase=='available'
            assert len(requests)==before
            with pytest.raises(CloudError):domain.materials.analysis(record.receive_id,sid)
            await manager.receives.retry(domain,record.receive_id,sid,refresh=True);await manager.drain()
            ready=await manager.receives.read(domain,record.receive_id,sid)
            assert ready.phase=='available' and [f.material_id for f in ready.files]==[f.material_id for f in record.files]
            assert ready.receive_id==record.receive_id and path.read_bytes()==data[report.artifact.artifact_id]
            assert domain.materials.analysis(record.receive_id,sid).receive_id==record.receive_id
            assert len(requests)>before
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())
