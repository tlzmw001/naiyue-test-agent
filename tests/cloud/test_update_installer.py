"""Trusted downloads and durable update identity, without real providers or Docker."""
import hashlib,json,os
from pathlib import Path
from uuid import uuid4

import pytest
from test_template_preparation import build_inputs
from lifecycle_helpers import template
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def inputs(tmp_path):
    from naiyue_test_agent.contracts.cloud_releases import CloudRelease
    from naiyue_test_agent.cloud_install.settings import UpdateSettings
    build=build_inputs(tmp_path);bundle=tmp_path/'bundle';bundle.mkdir()
    def artifact(path):
        target=bundle/path.name;target.write_bytes(path.read_bytes())
        return dict(filename=path.name,url='https://releases.invalid/'+path.name,size=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    manifest=CloudRelease(release_id='r2',agent_version='0.5.6',source_commit='a'*40,coordinator_protocol=1,
        runner_protocol=1,state_schema=1,min_connector_protocol=1,wheels={'linux-arm64':artifact(build.wheel)},
        kit=artifact(build.kit_wheel),kit_version='0.6.0',test_lock=artifact(build.test_lock),base_image=build.base_image)
    path=bundle/'r2.json';path.write_text(manifest.model_dump_json());path.chmod(0o600)
    index=bundle/'index.json';index.write_text(json.dumps(dict(schema_version=1,releases={'r2':artifact(path)})));index.chmod(0o600)
    connection=tmp_path/'connection.json';connection.write_text('{}');connection.chmod(0o600)
    settings=UpdateSettings(work_dir=tmp_path/'updates',index=str(index),trusted_prefixes=['https://releases.invalid/'],
        platform='linux-arm64',build=build,connection_file=connection)
    return settings,manifest


class Owner:
    def __init__(self):
        from types import SimpleNamespace
        self.posts=[];self.revision=0;self.settings=SimpleNamespace(instance_id='fixture')
    def request(self,method,path,**kw):
        if path=='/instance':return dict(instance_id='fixture',protocol=1,version='0.5.6',capabilities=['releases:write'])
        if path.startswith('/releases/active/'):return dict(release_id=None,revision=self.revision)
        self.posts.append((path,kw['json']))
        if path=='/releases/activate':self.revision+=1
        return dict(phase='ready')


def test_local_manifest_and_files_are_verified_and_original_manifest_cannot_drift(tmp_path):
    from naiyue_test_agent.cloud_install.source import load_release,artifact_file
    settings,manifest=inputs(tmp_path)
    found,fingerprint=load_release(settings,'r2')
    assert found==manifest
    wheel=artifact_file(settings,manifest.wheels[settings.platform])
    assert wheel.read_bytes()==(Path(settings.index).parent/manifest.wheels[settings.platform].filename).read_bytes()
    wheel.write_bytes(b'corrupt cache')
    assert artifact_file(settings,manifest.wheels[settings.platform]).read_bytes()==settings.build.wheel.read_bytes()
    (Path(settings.index).parent/manifest.kit.filename).write_bytes(b'bad bytes')
    with pytest.raises(CloudError,match='release_artifact_mismatch'):artifact_file(settings,manifest.kit)
    with pytest.raises(CloudError,match='release_identity_conflict'):load_release(settings,'r2',expected_sha256='0'*64)


def test_remote_origin_prefix_is_authorization_not_just_https(tmp_path):
    from naiyue_test_agent.cloud_install.source import authorize_url
    settings,_=inputs(tmp_path)
    for url in ['https://releases.invalid.evil/x','https://evil.invalid/x','https://releases.invalid/%2e%2e/x','https://u:p@releases.invalid/x']:
        with pytest.raises(CloudError,match='release_source_forbidden'):authorize_url(settings,url)
    assert authorize_url(settings,'https://releases.invalid/agent.whl')=='https://releases.invalid/agent.whl'


def test_update_resume_reuses_verified_build_and_original_activation_identity(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.worker import execute
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cloud_install.worker as worker
    settings,manifest=inputs(tmp_path);store=UpdateStore(settings);ident=str(uuid4());store.create(ident,'r2')
    built=[];owner=Owner()
    receipt=template(runner_release_id='r2',wheel_sha256=manifest.wheels[settings.platform].sha256,kit_wheel_sha256=manifest.kit.sha256)
    def prepare(build,release_id):
        built.append(release_id);build.output.write_text(receipt.model_dump_json());build.output.chmod(0o600);return receipt
    monkeypatch.setattr(worker,'prepare_template',prepare)
    execute(settings,ident,client=owner)
    assert store.read(ident)['phase']=='completed' and built==['r2']
    execute(settings,ident,client=owner)
    assert built==['r2'] and [p for p,_ in owner.posts]==['/releases/register','/releases/activate']
    with pytest.raises(CloudError,match='command_id_conflict'):store.create(ident,'other')


def test_build_failure_keeps_owner_active_unchanged_and_fails_visibly(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.worker import execute
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cloud_install.worker as worker
    settings,_=inputs(tmp_path);ident=str(uuid4());store=UpdateStore(settings);store.create(ident,'r2');owner=Owner()
    def fail(*a):raise CloudError('template_preflight_failed')
    monkeypatch.setattr(worker,'prepare_template',fail)
    execute(settings,ident,client=owner)
    assert store.read(ident)['error_code']=='template_preflight_failed'
    assert store.read(ident)['phase']=='failed' and owner.posts==[] and owner.revision==0


def test_one_selected_manifest_drives_compatibility_and_build(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.worker import execute
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cloud_install.worker as worker
    settings,manifest=inputs(tmp_path);ident=str(uuid4());store=UpdateStore(settings);store.create(ident,'r2');calls=[]
    incompatible=manifest.model_copy(update={'coordinator_protocol':2})
    selected=[incompatible,manifest]
    monkeypatch.setattr(worker,'load_release',lambda *a,**kw:(selected.pop(0),'c'*64))
    def prepare(*a):calls.append(True);return template(runner_release_id='r2',wheel_sha256=manifest.wheels[settings.platform].sha256,kit_wheel_sha256=manifest.kit.sha256)
    monkeypatch.setattr(worker,'prepare_template',prepare)
    owner=Owner();execute(settings,ident,client=owner)
    assert store.read(ident)['phase']=='failed'
    assert store.read(ident)['error_code']=='release_protocol_incompatible' and calls==[] and owner.posts==[]


def test_same_command_cannot_be_retargeted_to_another_template(tmp_path):
    from naiyue_test_agent.cloud_install.store import UpdateStore
    settings,_=inputs(tmp_path);ident=str(uuid4());UpdateStore(settings).create(ident,'r2')
    changed=settings.model_copy(update={'build':settings.build.model_copy(update={'template_id':'other'})})
    with pytest.raises(CloudError,match='command_id_conflict'):UpdateStore(changed).create(ident,'r2')


def test_https_redirect_cannot_escape_trusted_prefix_and_download_proxy_is_explicit(tmp_path,monkeypatch):
    import httpx
    import naiyue_test_agent.cloud_install.source as source
    from pydantic import SecretStr
    settings,manifest=inputs(tmp_path);settings=settings.model_copy(update={'index':'https://releases.invalid/index.json',
        'download_proxy':SecretStr('http://approved-proxy.invalid:8080')})
    seen=[];original=httpx.Client
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302,headers={'location':'https://evil.invalid/index.json'})
    def client(**kwargs):
        assert kwargs.pop('proxy')=='http://approved-proxy.invalid:8080'
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(source.httpx,'Client',client)
    with pytest.raises(CloudError,match='release_source_forbidden'):source.load_release(settings,'r2')
    assert seen==['https://releases.invalid/index.json']


def test_verified_manifest_can_resume_when_catalog_moves_on(tmp_path):
    from naiyue_test_agent.cloud_install.source import load_release
    settings,manifest=inputs(tmp_path);_,sha=load_release(settings,'r2')
    Path(settings.index).write_text(json.dumps(dict(schema_version=1,releases={})))
    assert load_release(settings,'r2',expected_sha256=sha)==(manifest,sha)


def test_declared_coordinator_minimum_requires_maintenance_before_build(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.worker import execute
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cloud_install.worker as worker
    settings,manifest=inputs(tmp_path);ident=str(uuid4());store=UpdateStore(settings);store.create(ident,'r2')
    selected=manifest.model_copy(update={'min_coordinator_version':'0.5.7'})
    monkeypatch.setattr(worker,'load_release',lambda *a,**kw:(selected,'c'*64));built=[]
    monkeypatch.setattr(worker,'prepare_template',lambda *a:built.append(True))
    owner=Owner();execute(settings,ident,client=owner)
    assert store.read(ident)['error_code']=='release_protocol_incompatible' and built==[] and owner.posts==[]


def test_operator_configuration_does_not_require_obsolete_artifact_paths(tmp_path):
    settings,_=inputs(tmp_path)
    serialized=settings.model_dump(mode='json')['build']
    assert not set(serialized)&{'wheel','wheel_sha256','kit_wheel','kit_profile','test_lock','base_image','output'}
