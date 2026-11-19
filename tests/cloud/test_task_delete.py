import asyncio
from uuid import uuid4

import httpx
import pytest

from cloud_helpers import owner_headers
from lifecycle_helpers import Backend, command, config, register
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError, TaskAttachment
from naiyue_test_agent.service.cloud.app import create_cloud_apps


def request(runtime, task_id, **changes):
    preview = runtime.deletions.preview(task_id)
    return dict(command_id=str(uuid4()), confirm_task_id=task_id,
        expected_revision=preview['revision'], expected_generation=preview['generation'],
        preview_digest=preview['preview_digest'], acknowledge_unknown=False) | changes


def test_delete_cleans_only_owned_task_and_preserves_shared_input_and_original_id(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime); other = register(runtime)
                runtime.history_page(task.task_id)
                shared = runtime.settings.data_dir / 'inputs' / task.input_id
                parent = runtime.settings.data_dir / 'tasks' / task.task_id
                parent.mkdir(parents=True); (parent / 'report').write_text('remove')
                original = request(runtime, task.task_id)
                receipt = await runtime.deletions.command(task.task_id, original)
                assert receipt.status == 'succeeded' and receipt.result['phase'] == 'finished'
                tombstone = receipt.result['tombstone']
                assert tombstone['instance_id'] == runtime.instance_id
                assert tombstone['task_id'] == task.task_id
                assert tombstone['delete_command_id'] == original['command_id']
                assert not parent.exists() and shared.exists()
                assert runtime.registry.get_task(other.task_id).lifecycle != 'deleted'
                assert runtime.registry.get_task(task.task_id).lifecycle == 'deleted'
                assert runtime.resources.capacity()['counts']['retained'] == 1
                assert await runtime.deletions.command(task.task_id, original) == receipt
                with pytest.raises(CloudError, match='task_not_available'):
                    runtime.history_page(task.task_id)
                assert not (runtime.settings.data_dir / 'history' / (task.task_id + '.sqlite')).exists()
        asyncio.run(run())


@pytest.mark.parametrize('field,value', [('active_run_id', 'a' * 32), ('reclaim_blockers', ['required_save']),
    ('artifact_operation_id', str(uuid4())), ('control_operation_id', str(uuid4()))])
def test_normal_delete_requires_stop_and_necessary_save(tmp_path, field, value):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime)
                runtime.replace_task(task.model_copy(update={field: value, 'revision': 2}),
                    expected_revision=1, expected_generation=1)
                with pytest.raises(CloudError, match='task_delete_requires_stop'):
                    await runtime.deletions.command(task.task_id, request(runtime, task.task_id))
                assert runtime.registry.get_task(task.task_id).lifecycle != 'deleting'
        asyncio.run(run())


def test_stale_confirmation_and_wrong_task_fail_before_delete(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime); original = request(runtime, task.task_id)
                with pytest.raises(CloudError, match='task_delete_confirmation_changed'):
                    await runtime.deletions.command(task.task_id, original | {'confirm_task_id': 'f' * 32})
                runtime.replace_task(task.model_copy(update={'revision': 2}), expected_revision=1, expected_generation=1)
                with pytest.raises(CloudError, match='task_revision_conflict'):
                    await runtime.deletions.command(task.task_id, original)
        asyncio.run(run())


def test_owner_routes_authenticate_and_task_listener_has_no_delete(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task = register(runtime); owner, internal = create_cloud_apps(runtime)
                route = '/api/cloud/v1/tasks/' + task.task_id
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin) as c:
                    assert (await c.get(route + '/delete-preview')).status_code == 401
                    preview = await c.get(route + '/delete-preview', headers=owner_headers(runtime))
                    assert preview.status_code == 200
                    response = await c.post(route + '/delete', headers=owner_headers(runtime), json=request(runtime, task.task_id))
                    assert response.status_code == 200 and response.json()['status'] == 'succeeded'
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=internal), base_url='http://' + runtime.settings.task_listen) as c:
                    assert (await c.post(route + '/delete', json={})).status_code == 404
        asyncio.run(run())


def test_delete_reserves_shared_input_candidates_before_clearing_task(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            from naiyue_test_agent.contracts.cloud_events import digest
            from naiyue_test_agent.contracts.cloud_delete import DeleteTask
            async with open_cloud_backend(runtime, backend=Backend()):
                task=register(runtime)
                aid=str(uuid4())
                with_attachment=task.model_copy(update={'revision':task.revision+1,'attachments':{
                    aid:TaskAttachment(attachment_id=aid,input_id=task.input_id,display_name='shared',size=1)}})
                runtime.replace_task(with_attachment,
                                     expected_revision=task.revision,expected_generation=task.generation)
                original=request(runtime,task.task_id)
                fingerprint=digest(dict(task_id=task.task_id,request=original))
                reserved,fresh=runtime.deletions.reserve(task.task_id,DeleteTask.model_validate(original),fingerprint)
                assert fresh and reserved.result['input_candidates']==[task.input_id]
                assert runtime.registry.get_task(task.task_id).attachments
                done=await runtime.deletions.reconcile(original['command_id'])
                assert done.status=='succeeded' and done.result['input_candidates']==[task.input_id]
                assert not runtime.registry.get_task(task.task_id).attachments
        asyncio.run(run())


def test_delete_last_input_reference_reclaims_package_once(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                task=register(runtime)
                package=runtime.inputs.get_input(task.input_id)
                archive=runtime.settings.data_dir/'inputs'/task.input_id
                original=request(runtime,task.task_id)
                deleted=await runtime.deletions.command(task.task_id,original)
                assert deleted.status=='succeeded'
                assert deleted.result['input_cleanup']['released_quota_bytes']==package.size
                assert not archive.exists()
                assert await runtime.deletions.reconcile(original['command_id'])==deleted
        asyncio.run(run())


def test_owner_use_protects_cache_hit_until_explicit_release(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime,backend=Backend()):
                task=register(runtime)
                owner,_=create_cloud_apps(runtime)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                                             base_url=runtime.settings.origin,headers=owner_headers(runtime)) as client:
                    use_id=str(uuid4());operation_id=str(uuid4())
                    body=dict(command_id=use_id,input_id=task.input_id,purpose='associate',origin_operation_id=operation_id)
                    held=await client.post('/api/cloud/v1/input-uses',json=body)
                    assert held.status_code==200,held.text
                    assert held.json()['state']=='held'
                    cache=await client.get('/api/cloud/v1/inputs/'+task.input_id)
                    assert cache.status_code==200
                    original=request(runtime,task.task_id)
                    deleted=await runtime.deletions.command(task.task_id,original)
                    cleanup=deleted.result['input_cleanup']
                    assert deleted.status=='succeeded' and cleanup['released_quota_bytes']==0
                    assert cleanup['candidates'][0]['reason']=='input_use_held'
                    path='/api/cloud/v1/commands/'+deleted.command_id
                    assert (await client.get(path)).json()['result']['input_cleanup']==cleanup
                    released=await client.delete('/api/cloud/v1/input-uses/'+use_id)
                    assert released.json()['state']=='released'
                    assert (await client.get(path)).json()['result']['input_cleanup']==cleanup
                    final=await client.post(path+'/reconcile',json={})
                    assert final.json()['result']['input_cleanup']['released_quota_bytes']==cache.json()['size']
        asyncio.run(run())


def test_cleanup_io_failure_preserves_task_success_and_resumes_same_generation(monkeypatch,tmp_path):
    import errno
    from naiyue_test_agent.application.cloud import input_reclamation

    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime,backend=Backend()):
                task=register(runtime);size=runtime.inputs.get_input(task.input_id).size
                unlink=input_reclamation.os.unlink
                def invalid(name,*args,**kwargs):
                    if name=='input.json':raise OSError(errno.EACCES,'controlled input failure')
                    return unlink(name,*args,**kwargs)
                monkeypatch.setattr(input_reclamation.os,'unlink',invalid)
                original=request(runtime,task.task_id)
                first=await runtime.deletions.command(task.task_id,original)
                assert first.status=='succeeded'
                assert runtime.registry.get_task(task.task_id).lifecycle=='deleted'
                cleanup=first.result['input_cleanup']
                assert cleanup['state']=='failed' and cleanup['released_quota_bytes']==0
                assert cleanup['candidates'][0]['reason']['errno']==errno.EACCES
                from naiyue_test_agent.contracts.cloud_inputs import AcquireInputUse
                use_id=str(uuid4())
                with pytest.raises(CloudError,match='input_retiring'):
                    runtime.inputs.uses.acquire(AcquireInputUse(command_id=use_id,input_id=task.input_id,
                        purpose='associate',origin_operation_id=use_id),runtime.inputs.limits,runtime.settings.max_receipts)
                monkeypatch.setattr(input_reclamation.os,'unlink',unlink)
                final=await runtime.deletions.reconcile(first.command_id)
                assert final.status=='succeeded' and final.result['input_cleanup']['state']=='completed'
                assert final.result['input_cleanup']['released_quota_bytes']==size
                assert await runtime.deletions.reconcile(first.command_id)==final
        asyncio.run(run())


def test_migrated_v1_archive_stays_protected_when_old_recovery_is_unverifiable(tmp_path):
    import json
    cloud=config(tmp_path)
    with open_cloud_runtime(cloud) as first:
        task=register(first)
        with first.database.transaction(write=True) as db:
            db.execute("DELETE FROM meta WHERE key LIKE 'input-state:%' OR key LIKE 'transfer-generation:%'")
            db.execute('PRAGMA user_version=2')
    marker=cloud.data_dir/'cloud-root.json'
    data=json.loads(marker.read_text());marker.write_text(json.dumps(data | {'schema':2}))
    with open_cloud_runtime(cloud) as runtime:
        async def run():
            async with open_cloud_backend(runtime,backend=Backend()):
                result=await runtime.deletions.command(task.task_id,request(runtime,task.task_id))
                assert result.status=='succeeded'
                cleanup=result.result['input_cleanup']
                assert cleanup['released_quota_bytes']==0 and cleanup['candidates'][0]['reason']=='input_legacy_use_unverified'
                assert runtime.inputs.get_input(task.input_id).size>0
                assert runtime.owner.record['schema']==3
        asyncio.run(run())


def test_old_reconcile_cannot_remove_reuploaded_equivalent_generation(tmp_path):
    from test_input_recovery import uploaded
    from naiyue_test_agent.cloud_files.capture import capture
    from naiyue_test_agent.contracts.cloud_inputs import Selection

    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime,backend=Backend()):
                task=register(runtime)
                result=await runtime.deletions.command(task.task_id,request(runtime,task.task_id))
                assert result.result['input_cleanup']['candidates'][0]['state']=='reclaimed'
                source=tmp_path/'reimport';source.mkdir()
                (source/'fixture.txt').write_text('prepared input fixture')
                captured=capture(source,Selection(paths=['fixture.txt']),tmp_path/'new-capture',runtime.settings.input_limits)
                assert captured.input_id==task.input_id
                transfer=uploaded(runtime,captured)
                replacement=runtime.inputs.commit(transfer.transfer_id)
                assert await runtime.deletions.reconcile(result.command_id)==result
                assert runtime.inputs.get_input(task.input_id)==replacement
        asyncio.run(run())


@pytest.mark.parametrize('path,method', [('inputs', 'GET'), ('transfers', 'POST')])
def test_pending_package_metadata_read_blocks_reclamation(monkeypatch,tmp_path,path,method):
    from threading import Event

    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime,backend=Backend()):
                task=register(runtime)
                package=runtime.inputs.get_input(task.input_id)
                original=runtime.inputs._package
                loop=asyncio.get_running_loop()
                opened=asyncio.Event()
                resume=Event()

                def wait_after_index(*args,**kwargs):
                    loop.call_soon_threadsafe(opened.set)
                    if not resume.wait(10):
                        raise TimeoutError('input reader was not released')
                    return original(*args,**kwargs)

                monkeypatch.setattr(runtime.inputs,'_package',wait_after_index)
                owner,_=create_cloud_apps(runtime)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                        base_url=runtime.settings.origin,headers=owner_headers(runtime)) as client:
                    endpoint='/api/cloud/v1/'+path
                    options={}
                    if method=='GET':
                        endpoint+='/'+task.input_id
                    else:
                        options['json']=dict(command_id=str(uuid4()),purpose='input',input_id=task.input_id,
                            size=package.size,archive_sha256=package.archive_sha256,codec=package.codec)
                    read=asyncio.create_task(client.request(method,endpoint,**options))
                    try:
                        await asyncio.wait_for(opened.wait(),timeout=5)
                        deleted=await runtime.deletions.command(task.task_id,request(runtime,task.task_id))
                    finally:
                        resume.set()
                        response=await read
                    assert deleted.status=='succeeded'
                    cleanup=deleted.result['input_cleanup']
                    assert cleanup['released_quota_bytes']==0
                    assert cleanup['candidates'][0]['reason']=='input_io_active'
                    assert response.status_code==(200 if method=='GET' else 201),response.text
                    reconciled=await runtime.deletions.reconcile(deleted.command_id)
                    assert reconciled.result['input_cleanup']['released_quota_bytes']==package.size
        asyncio.run(run())


def test_legacy_cutover_proof_enables_reclaim_only_after_registered_use_ends(tmp_path):
    from naiyue_test_agent.storage.cloud.input_lifecycle import package_state,put

    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime,backend=Backend()):
                task=register(runtime)
                with runtime.database.transaction(write=True) as db:
                    put(db,'input-state:'+task.input_id,package_state(db,task.input_id)|dict(legacy_unverified=True))
                owner,_=create_cloud_apps(runtime)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),
                                             base_url=runtime.settings.origin,headers=owner_headers(runtime)) as client:
                    base='/api/cloud/v1'
                    use=str(uuid4())
                    proof=dict(command_id=str(uuid4()),sources_verified=True,input_use_ids=[use])
                    endpoint=base+'/input-legacy-cutover'
                    assert (await client.post(endpoint,json=proof)).status_code==409
                    assert (await client.post(base+'/input-uses',json=dict(command_id=use,input_id=task.input_id,
                        purpose='recover',origin_operation_id=str(uuid4())))).status_code==200
                    deleted=await runtime.deletions.command(task.task_id,request(runtime,task.task_id))
                    assert deleted.result['input_cleanup']['candidates'][0]['reason']=='input_legacy_use_unverified'
                    assert (await client.post(endpoint,json=proof,headers={'Authorization':'invalid'})).status_code==401
                    applied=await client.post(endpoint,json=proof)
                    assert applied.status_code==200,applied.text
                    assert (runtime.settings.data_dir/'inputs'/task.input_id/'archive').is_file()
                    checked=await runtime.deletions.reconcile(deleted.command_id)
                    assert checked.result['input_cleanup']['candidates'][0]['reason']=='input_use_held'
                    assert (await client.delete(base+'/input-uses/'+use)).status_code==200
                    assert (await client.post(endpoint,json=proof)).json()==applied.json()
                    final=await runtime.deletions.reconcile(deleted.command_id)
                    assert final.result['input_cleanup']['candidates'][0]['state']=='reclaimed'
                    assert final.result['input_cleanup']['released_quota_bytes']>0
        asyncio.run(run())
