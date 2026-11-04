"""Explicit releases preserve active selection and pinned task identities."""
from uuid import uuid4

import pytest
from pydantic import ValidationError
from cloud_helpers import settings
from lifecycle_helpers import config, register, template
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def release(name='r2'):
    from naiyue_test_agent.contracts.cloud_releases import CloudRelease
    artifact=dict(filename='agent.whl',url='https://release.invalid/files/agent.whl',size=12,sha256='3'*64)
    return CloudRelease(release_id=name,agent_version='0.5.6',source_commit='a'*40,
        coordinator_protocol=1,runner_protocol=1,state_schema=1,min_connector_protocol=1,
        wheels={'linux-arm64':artifact},kit=dict(artifact,filename='aitest_kit.whl',sha256=template().kit_wheel_sha256),
        kit_version=template().kit_version,test_lock=dict(artifact,filename='test.lock',sha256='6'*64),
        base_image=template().base_image)


def test_release_record_activate_is_cas_idempotent_and_pins_old_tasks(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        old=register(r);target=release();receipt=template(runner_release_id='r2')
        command=str(uuid4())
        value=dict(command_id=command,release=target.model_dump(),template=receipt.model_dump())
        record=r.releases.register(value)
        assert r.releases.register(value)==record
        activate=dict(command_id=str(uuid4()),release_id='r2',template_id=receipt.template_id,expected_revision=0)
        chosen=r.releases.activate(activate)
        assert r.releases.activate(activate)==chosen
        assert r.releases.select(receipt.template_id)==receipt
        assert r.registry.get_task(old.task_id).runner_release_id==old.runner_release_id
        assert r.registry.get_task(old.task_id).template_snapshot==old.template_snapshot
        with pytest.raises(CloudError,match='release_revision_conflict'):
            r.releases.activate(activate|dict(command_id=str(uuid4())))
        with pytest.raises(CloudError,match='release_identity_conflict'):
            r.releases.register(value|dict(command_id=str(uuid4()),release=target.model_copy(update={'source_commit':'b'*40}).model_dump()))
    with open_cloud_runtime(config(tmp_path)) as r:
        assert r.releases.select(receipt.template_id)==receipt


@pytest.mark.parametrize('field,value', [('image_id','sha256:'+'9'*64),('wheel_sha256','9'*64),('kit_version','wrong')])
def test_register_cannot_lie_about_verified_release_inputs(tmp_path,field,value):
    with open_cloud_runtime(config(tmp_path)) as r:
        # Image IDs are build outputs; the other fields must match trusted release inputs.
        if field=='image_id':
            r.releases.register(dict(command_id=str(uuid4()),release=release().model_dump(),template=template(runner_release_id='r2',image_id=value).model_dump()))
        else:
            with pytest.raises(CloudError,match='release_template_mismatch'):
                r.releases.register(dict(command_id=str(uuid4()),release=release().model_dump(),template=template(runner_release_id='r2',**{field:value}).model_dump()))


def test_manifest_refuses_unsafe_artifact_names_and_urls():
    from naiyue_test_agent.contracts.cloud_releases import ReleaseArtifact
    for values in [dict(filename='../x',url='https://release.invalid/x'),dict(filename='x',url='https://u:p@release.invalid/x'),dict(filename='x',url='http://release.invalid/x')]:
        with pytest.raises(ValidationError):ReleaseArtifact(**values,size=1,sha256='1'*64)


def test_maintenance_blocks_new_work_and_survives_restart(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        old=register(r)
        r.registry.save_task_cas(old.model_copy(update={'lifecycle':'stopped'}),expected_revision=old.revision,expected_generation=old.generation,revoked_at=0)
        entered=r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))
        assert entered['maintenance']=='ready'
        from naiyue_test_agent.contracts.cloud_tasks import CreateProject
        with pytest.raises(CloudError,match='cloud_maintenance'):
            r.commands.create_project(CreateProject(command_id=str(uuid4()),name='blocked'))
    with open_cloud_runtime(config(tmp_path)) as r:
        assert r.releases.maintenance_status()['maintenance']=='ready'
        r.releases.maintenance(dict(command_id=str(uuid4()),action='leave'))
        assert r.commands.create_project(CreateProject(command_id=str(uuid4()),name='allowed')).status=='succeeded'


def test_declared_missing_capability_never_becomes_ready(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        value=release().model_copy(update={'required_capabilities':['imaginary:capability']})
        with pytest.raises(CloudError,match='release_protocol_incompatible'):
            r.releases.register(dict(command_id=str(uuid4()),release=value.model_dump(),template=template(runner_release_id='r2').model_dump()))
        assert r.releases.repository.list()==[]


def test_only_process_io_leases_are_retired_after_exclusive_reopen(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        lease=r.releases.enter_io()
        assert lease
        assert r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))['maintenance']=='draining'
    with open_cloud_runtime(config(tmp_path)) as r:
        assert r.releases.maintenance_status()['maintenance']=='ready'


def test_active_is_selected_in_final_task_creation_transaction(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        old=register(r)
        target=template(runner_release_id='r2')
        r.releases.register(dict(command_id=str(uuid4()),release=release().model_dump(),template=target.model_dump()))
        r.releases.activate(dict(command_id=str(uuid4()),release_id='r2',template_id=target.template_id,expected_revision=0))
        created=r.inputs.create_task(dict(command_id=str(uuid4()),project_id=old.project_id,input_id=old.input_id,template_id=target.template_id,title='new'))
        assert created.result['task']['runner_release_id']=='r2'
        assert created.result['task']['template_snapshot']==target.model_dump()
        assert r.registry.get_task(old.task_id)==old


def test_release_identity_is_global_across_logical_templates(tmp_path):
    from cloud_helpers import settings
    first=template();second=template(template_id='second')
    with open_cloud_runtime(settings(tmp_path,templates={'python-tests':first,'second':second})) as r:
        r.releases.register(dict(command_id=str(uuid4()),release=release().model_dump(),template=first.model_copy(update={'runner_release_id':'r2'}).model_dump()))
        with pytest.raises(CloudError,match='release_identity_conflict'):
            r.releases.register(dict(command_id=str(uuid4()),release=release().model_copy(update={'source_commit':'b'*40}).model_dump(),
                template=second.model_copy(update={'runner_release_id':'r2'}).model_dump()))


def test_same_image_accepts_a_fresh_preflight_observation_without_rebinding_identity(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        target=template(runner_release_id='r2')
        first=r.releases.register(dict(command_id=str(uuid4()),release=release().model_dump(),template=target.model_dump()))
        second=r.releases.register(dict(command_id=str(uuid4()),release=release().model_dump(),template=target.model_copy(update={'preflight_sha256':'9'*64}).model_dump()))
        assert second==first and r.releases.repository.list()==[first]


def test_activation_and_startup_recheck_support_after_coordinator_downgrade(tmp_path,monkeypatch):
    import naiyue_test_agent
    value=release().model_copy(update={'min_coordinator_version':'0.5.7'})
    monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.7')
    with open_cloud_runtime(config(tmp_path)) as r:
        r.releases.register(dict(command_id=str(uuid4()),release=value.model_dump(),template=template(runner_release_id='r2').model_dump()))
        r.releases.activate(dict(command_id=str(uuid4()),release_id='r2',template_id='python-tests',expected_revision=0))
        monkeypatch.setattr(naiyue_test_agent,'__version__','0.5.6')
        with pytest.raises(CloudError,match='release_protocol_incompatible'):
            r.releases.activate(dict(command_id=str(uuid4()),release_id='r2',template_id='python-tests',expected_revision=1))
    with pytest.raises(CloudError,match='release_protocol_incompatible'):
        with open_cloud_runtime(config(tmp_path)):pytest.fail('unsupported active release accepted')
