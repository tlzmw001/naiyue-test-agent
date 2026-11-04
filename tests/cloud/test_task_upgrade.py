import asyncio
from uuid import uuid4
import pytest
from lifecycle_helpers import config,register,template,Backend
from test_release_updates import release
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.contracts.cloud_lifecycle import EnvironmentCommand


def prepare(r):
    old=register(r);old=old.model_copy(update={'lifecycle':'stopped','environment_phase':'absent','state_epoch':str(uuid4())})
    r.registry.save_task_cas(old,expected_revision=old.revision,expected_generation=old.generation,revoked_at=0)
    r.releases.register(dict(command_id=str(uuid4()),release=release().model_dump(),template=template(runner_release_id='r2').model_dump()))
    return old


def test_upgrade_is_fenced_bound_to_backup_and_preserves_state_epoch(tmp_path):
    async def scenario():
        from naiyue_test_agent.application.cloud.upgrades import reserve_upgrade,commit_upgrade
        with open_cloud_runtime(config(tmp_path)) as r:
            async with open_cloud_backend(r,backend=Backend()):
                old=prepare(r);request=dict(command_id=str(uuid4()),release_id='r2',expected_revision=old.revision,expected_generation=old.generation)
                result=await reserve_upgrade(r,old.task_id,request)
                assert await reserve_upgrade(r,old.task_id,request)==result
                pending=r.registry.get_task(old.task_id);assert pending.environment_operation_id==request['command_id']
                backup=dict(command_id=request['command_id'],task_id=old.task_id,generation=old.generation,
                    reservation_sha256=digest(result['reservation']),manifest_sha256='b'*64,files=2,bytes=30)
                with pytest.raises(CloudError,match='upgrade_backup_identity_mismatch'):
                    commit_upgrade(r,old.task_id,backup|dict(generation=old.generation+1))
                done=commit_upgrade(r,old.task_id,backup)
                assert commit_upgrade(r,old.task_id,backup)==done
                current=r.registry.get_task(old.task_id)
                assert current.runner_release_id=='r2' and current.state_epoch==old.state_epoch
                assert current.generation==old.generation and current.environment_operation_id is None
                assert current.lifecycle=='stopped' and done['phase']=='committed'
    asyncio.run(scenario())


@pytest.mark.parametrize('change',[dict(active_run_id='c'*32),dict(sandbox_id='still-present'),dict(reclaim_blockers=['background_commands']),dict(lifecycle='ready')])
def test_upgrade_refuses_non_stopped_or_unconfirmed_task(tmp_path,change):
    async def scenario():
        from naiyue_test_agent.application.cloud.upgrades import reserve_upgrade
        with open_cloud_runtime(config(tmp_path)) as r:
            async with open_cloud_backend(r,backend=Backend()):
                old=prepare(r);bad=old.model_copy(update=change)
                r.registry.save_task_cas(bad,expected_revision=old.revision,expected_generation=old.generation,revoked_at=0)
                with pytest.raises(CloudError,match='task_upgrade_requires_stop'):
                    await reserve_upgrade(r,old.task_id,dict(command_id=str(uuid4()),release_id='r2',expected_revision=old.revision,expected_generation=old.generation))
    asyncio.run(scenario())


def test_cancel_releases_original_pin_and_delete_cannot_bypass_pending_upgrade(tmp_path):
    async def scenario():
        from naiyue_test_agent.application.cloud.upgrades import reserve_upgrade,cancel_upgrade
        with open_cloud_runtime(config(tmp_path)) as r:
            async with open_cloud_backend(r,backend=Backend()):
                old=prepare(r);ident=str(uuid4())
                reserved=await reserve_upgrade(r,old.task_id,dict(command_id=ident,release_id='r2',expected_revision=old.revision,expected_generation=old.generation))
                preview=r.deletions.preview(old.task_id)
                with pytest.raises(CloudError,match='task_upgrade_pending'):
                    await r.deletions.command(old.task_id,dict(command_id=str(uuid4()),confirm_task_id=old.task_id,
                        expected_revision=preview['revision'],expected_generation=preview['generation'],preview_digest=preview['preview_digest'],acknowledge_unknown=True))
                assert cancel_upgrade(r,old.task_id,dict(command_id=ident,task_id=old.task_id,generation=old.generation,reservation_sha256=digest(reserved['reservation']),container_name='nyt-upgrade-'+ident.replace('-','')))['phase']=='cancelled'
                assert r.registry.get_task(old.task_id).environment_operation_id is None
                assert r.registry.get_task(old.task_id).runner_release_id==old.runner_release_id
    asyncio.run(scenario())


@pytest.mark.parametrize('stage',['reserve','commit'])
def test_inactive_ready_upgrade_rechecks_current_coordinator_support(tmp_path,monkeypatch,stage):
    import naiyue_test_agent
    from naiyue_test_agent.application.cloud.upgrades import reserve_upgrade,commit_upgrade,cancel_upgrade
    async def scenario():
        monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.7')
        with open_cloud_runtime(config(tmp_path)) as r:
            async with open_cloud_backend(r,backend=Backend()):
                old=register(r).model_copy(update={'lifecycle':'stopped','environment_phase':'absent'})
                r.registry.save_task_cas(old,expected_revision=old.revision,expected_generation=old.generation,revoked_at=0)
                target=release().model_copy(update={'min_coordinator_version':'0.5.7'})
                r.releases.register(dict(command_id=str(uuid4()),release=target.model_dump(),template=template(runner_release_id='r2').model_dump()))
                request=dict(command_id=str(uuid4()),release_id='r2',expected_revision=old.revision,expected_generation=old.generation)
                reserved=await reserve_upgrade(r,old.task_id,request) if stage=='commit' else None
                before=r.registry.get_task(old.task_id)
                monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.6')
                with pytest.raises(CloudError,match='release_protocol_incompatible'):
                    if stage=='reserve':await reserve_upgrade(r,old.task_id,request)
                    else:commit_upgrade(r,old.task_id,dict(command_id=request['command_id'],task_id=old.task_id,generation=old.generation,
                        reservation_sha256=digest(reserved['reservation']),manifest_sha256='b'*64,files=2,bytes=30))
                assert r.registry.get_task(old.task_id)==before
                if reserved:
                    assert cancel_upgrade(r,old.task_id,dict(command_id=request['command_id'],task_id=old.task_id,generation=old.generation,
                        reservation_sha256=digest(reserved['reservation']),container_name='nyt-upgrade-'+request['command_id'].replace('-','')))['phase']=='cancelled'
    asyncio.run(scenario())


@pytest.mark.parametrize('kind',['start','refresh','reconcile'])
def test_pinned_inactive_release_cannot_execute_after_coordinator_downgrade(tmp_path,monkeypatch,kind):
    import naiyue_test_agent
    from lifecycle_helpers import command
    async def scenario():
        monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.7')
        with open_cloud_runtime(config(tmp_path)) as r:
            backend=Backend()
            async with open_cloud_backend(r,backend=backend):
                old=register(r);pin=template(runner_release_id='r2')
                target=release().model_copy(update={'min_coordinator_version':'0.5.7'})
                r.releases.register(dict(command_id=str(uuid4()),release=target.model_dump(),template=pin.model_dump()))
                old=old.model_copy(update={'lifecycle':'stopped','environment_phase':'absent','runner_release_id':'r2',
                    'template_snapshot':pin,'template_digest':pin.fingerprint})
                r.registry.save_task_cas(old,expected_revision=old.revision,expected_generation=old.generation,revoked_at=0)
                request=command(old,'start' if kind=='reconcile' else kind)
                pending=None
                if kind=='reconcile':
                    parsed=EnvironmentCommand.model_validate(request)
                    fingerprint=digest(dict(task_id=old.task_id,request=parsed.model_dump(),idle_cutoff=None))
                    pending,_=r.lifecycle._reserve(old.task_id,parsed,fingerprint,None)
                monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.6')
                if pending:
                    result=await r.lifecycle.reconcile(pending.command_id)
                    assert result.status=='failed' and result.error_code=='release_protocol_incompatible'
                else:
                    with pytest.raises(CloudError,match='release_protocol_incompatible'):await r.lifecycle.command(old.task_id,request)
                assert backend.calls==[]
                current=r.registry.get_task(old.task_id)
                stopped=await r.lifecycle.command(old.task_id,command(current,'stop_environment'))
                assert stopped.status=='succeeded'
    asyncio.run(scenario())


def test_connected_inactive_runner_cannot_start_new_work_after_downgrade(tmp_path,monkeypatch):
    import naiyue_test_agent
    from test_runner_control import prepared,client,connect,hello
    from cloud_helpers import owner_headers
    monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.7')
    with open_cloud_runtime(config(tmp_path)) as r:
        pin=template(runner_release_id='r2')
        target=release().model_copy(update={'min_coordinator_version':'0.5.7'})
        r.releases.register(dict(command_id=str(uuid4()),release=target.model_dump(),template=pin.model_dump()))
        b=prepared(r).model_copy(update={'release_id':'r2'})
        task=r.registry.get_task(b.task_id)
        r.registry.save_task_cas(task.model_copy(update={'runner_release_id':'r2','template_snapshot':pin,'template_digest':pin.fingerprint}),
            expected_revision=task.revision,expected_generation=task.generation,revoked_at=0)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));welcome=ws.receive_json();fence={k:v for k,v in welcome.items() if k!='kind'}
            task=r.registry.get_task(b.task_id)
            monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.6')
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=dict(
                command_id=str(uuid4()),expected_revision=task.revision,expected_generation=task.generation,message='work',model_id='A'))
            assert response.status_code==409 and response.json()['error']['code']=='release_protocol_incompatible'
            assert r.registry.get_task(b.task_id)==task
            ws.send_json(dict(kind='heartbeat',**fence))
            assert ws.receive_json()['kind']=='welcome'
