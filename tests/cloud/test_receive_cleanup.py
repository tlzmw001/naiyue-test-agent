"""Explicit cache reclamation protects retained references and active IO."""
import asyncio
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud_receive import purge
from test_result_receive import receiving


def test_cleanup_keeps_shared_live_blobs_then_reclaims_orphans(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,*_=await receiving(tmp_path)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();files=domain.materials.files
            kept=list(files.directory('blobs').iterdir());assert kept
            orphan=files.blob('f'*64);orphan.write_bytes(b'orphan');orphan.chmod(0o600)
            stage=files.stage(str(uuid4()),str(uuid4()));stage.write_bytes(b'partial');stage.chmod(0o600)
            quarantine=files.directory('staging',first.receive_id)/('quarantine-'+uuid4().hex)
            quarantine.write_bytes(b'bad');quarantine.chmod(0o600)
            result=await manager.receives.cleanup(domain,sid)
            assert result==dict(removed_files=3,freed_bytes=16)
            assert all(p.exists() for p in kept) and not orphan.exists() and not stage.exists() and not quarantine.exists()
            with store.database.transaction(write=True) as c:purge(c,sid)
            result=await manager.receives.cleanup(domain,sid)
            assert result['removed_files']==len(kept) and all(not p.exists() for p in kept)
            assert (await manager.receives.cleanup(domain,sid))==dict(removed_files=0,freed_bytes=0)
            await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();assert list(files.directory('blobs').iterdir())
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_cleanup_preserves_failed_resume_and_refuses_active_read_or_producer(tmp_path):
    async def run():
        manager,domain,store,sid,remote,binding,rid,*_=await receiving(tmp_path,fail_segment=2)
        try:
            first=await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            await manager.drain();record=await manager.receives.read(domain,first.receive_id,sid)
            files=domain.materials.files;stage=files.stage(first.receive_id,record.files[0].artifact.artifact_id)
            before=stage.read_bytes();await manager.receives.cleanup(domain,sid);assert stage.read_bytes()==before
            with files.use():
                with pytest.raises(CloudError,match='cloud_receive_busy'):await manager.receives.cleanup(domain,sid)
            key=(domain.project_id,'active');manager.receives.flights[key]=asyncio.current_task()
            try:
                with pytest.raises(CloudError,match='cloud_receive_busy'):await manager.receives.cleanup(domain,sid)
            finally:manager.receives.flights.pop(key)
            await manager.receives.retry(domain,first.receive_id,sid);await manager.drain()
            assert (await manager.receives.read(domain,first.receive_id,sid)).phase=='available'
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_cleanup_rejects_link_without_touching_target(tmp_path):
    async def run():
        manager,domain,store,sid,remote,*_=await receiving(tmp_path)
        try:
            target=tmp_path/'outside';target.write_bytes(b'keep')
            domain.materials.files.blob('f'*64).symlink_to(target)
            with pytest.raises(CloudError,match='cloud_receive_cache_unavailable'):await manager.receives.cleanup(domain,sid)
            assert target.read_bytes()==b'keep'
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


def test_cleanup_route_auth_and_project_session_scope(tmp_path,monkeypatch):
    from test_manual_commands_routes import configured
    async def run():
        async with configured(monkeypatch,tmp_path) as t:
            path=t.base+'/cloud-receive-cache/cleanup'
            bad=await t.client.post(path,params=t.query,headers=t.headers|{'Authorization':''},json={})
            assert bad.status_code==401
            other=await t.client.post(path,params=t.query|{'local_session_id':t.other_sid},headers=t.headers,json={})
            assert other.status_code==404
            ok=await t.client.post(path,params=t.query,headers=t.headers,json={})
            assert ok.status_code==200 and ok.json()==dict(removed_files=0,freed_bytes=0)
    asyncio.run(run())


def test_cleanup_exclusion_lasts_until_cancelled_worker_finishes(tmp_path,monkeypatch):
    import threading
    from naiyue_test_agent.application.cloud_local import receive_cleanup
    async def run():
        manager,domain,store,sid,remote,binding,rid,*_=await receiving(tmp_path)
        entered=asyncio.Event();release=threading.Event();loop=asyncio.get_running_loop()
        original=receive_cleanup.reclaim
        def paused(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(receive_cleanup,'reclaim',paused)
        job=asyncio.create_task(manager.receives.cleanup(domain,sid))
        try:
            await asyncio.wait_for(entered.wait(),5)
            job.cancel()
            with pytest.raises(CloudError,match='cloud_receive_busy'):
                await manager.receives.submit(domain,binding['binding_id'],rid,dict(command_id=str(uuid4()),local_session_id=sid,artifact_ids=[]))
            with pytest.raises(CloudError,match='cloud_receive_busy'):
                with domain.materials.files.use():pass
            release.set()
            with pytest.raises(asyncio.CancelledError):await job
            assert not domain.materials.files.cleaning and domain.materials.files.users==0
        finally:
            release.set();await asyncio.gather(job,return_exceptions=True)
            await manager.close();remote.http.close();store.close()
    asyncio.run(run())
