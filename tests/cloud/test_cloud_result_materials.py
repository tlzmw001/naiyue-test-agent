"""Verified material reads are local, scoped, bounded and independent of remote deletion."""
import asyncio
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_result_receive import receiving


def test_material_reads_are_offline_scoped_lines_and_detect_lost_cache(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[files[2]['artifact']['artifact_id']]))
            await manager.drain();draft=await manager.receives.analysis(domain,first.receive_id,sid)
            report=next(v for v in draft.material_refs if v.role=='artifact')
            assert domain.materials.has_materials(sid)
            def offline(*a,**kw):raise AssertionError('material access must never contact owner')
            remote.request=offline;remote.task['lifecycle']='deleted'
            assert domain.materials.validate_refs(sid,[report.material_id])==[report]
            page=await domain.materials.query(sid,domain.project_id,dict(material_id=report.material_id,offset=2,limit=1))
            assert page['text']=='第二行\n' and page['next_offset']==3 and not page['complete'] and page['material']['reported'] is True
            domain.materials.limits=domain.materials.limits.model_copy(update={'max_read_lines':1})
            default=await domain.materials.query(sid,domain.project_id,dict(material_id=report.material_id,offset=2,limit=None))
            assert default['text']=='第二行\n'
            with pytest.raises(CloudError):domain.materials.validate_refs('other',[report.material_id])
            with pytest.raises(CloudError):await domain.materials.query(sid,str(uuid4()),dict(material_id=report.material_id,offset=1,limit=1))
            assert str(tmp_path) not in str(page)
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('replace',[False,True])
def test_material_read_pins_and_rechecks_the_verified_descriptor(tmp_path,monkeypatch,replace):
    import os
    from contextlib import contextmanager
    async def run():
        manager,domain,store,sid,remote,binding,rid,files,data,requests=await receiving(tmp_path)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[files[2]['artifact']['artifact_id']]))
            await manager.drain();refs=(await manager.receives.analysis(domain,first.receive_id,sid)).material_refs
            report=next(ref for ref in refs if ref.role=='artifact');original=domain.materials.files.verified
            @contextmanager
            def changed(path,*args,**kwargs):
                with original(path,*args,**kwargs) as stream:
                    before=path.stat()
                    if replace:
                        new=path.parent/'replacement';new.write_bytes(b'not the registered report');new.chmod(0o600);os.replace(new,path)
                    else:
                        with path.open('r+b') as output:output.write(b'X')
                        os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
                    yield stream
            monkeypatch.setattr(domain.materials.files,'verified',changed)
            with pytest.raises(CloudError,match='cloud_material_unavailable'):
                await domain.materials.query(sid,domain.project_id,dict(material_id=report.material_id,offset=1,limit=1))
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_unbound_memory_profile_has_no_material_port_or_disk_side_effect(tmp_path):
    from contextlib import AsyncExitStack
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.lifetime import ApplicationLifetime
    from naiyue_test_agent.application.cloud_local.materials import LocalMaterials
    async def run():
        with ProjectStore.in_memory() as store:
            materials=LocalMaterials(ApplicationLifetime(None,None,AsyncExitStack()),store,None)
            assert not materials.has_materials('S')
            with pytest.raises(CloudError,match='cloud_materials_unavailable'):materials.validate_refs('S',[str(uuid4())])
            with pytest.raises(CloudError,match='cloud_materials_unavailable'):
                await materials.query('S',None,dict(material_id=str(uuid4()),offset=1,limit=1))
        assert not list(tmp_path.iterdir())
    asyncio.run(run())
