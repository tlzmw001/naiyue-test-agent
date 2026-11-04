"""A configured channel feeds the existing update producer exactly once."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_update_installer import inputs,Owner


def channel(settings):
    path=Path(settings.index);value=json.loads(path.read_text());value['channels']={'stable':'r2'}
    path.write_text(json.dumps(value))


def test_channel_requires_an_existing_safe_release():
    from naiyue_test_agent.contracts.cloud_releases import ReleaseIndex
    with pytest.raises(ValidationError):ReleaseIndex(schema_version=1,releases={},channels={'stable':'missing'})


def test_duplicate_discovery_pins_manifest_and_reuses_original_job(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.discovery import discover
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cloud_install.discovery as discovery
    settings,manifest=inputs(tmp_path);channel(settings);owner=Owner();launched=[]
    monkeypatch.setattr(discovery,'launch',lambda path,ident:launched.append(ident) or UpdateStore(settings).read(ident))
    first=discover(settings,owner,settings_file=tmp_path/'update.json')
    second=discover(settings,owner,settings_file=tmp_path/'update.json')
    assert first['command_id']==second['command_id'] and len(set(launched))==1
    job=UpdateStore(settings).read(first['command_id'])
    assert job['manifest_sha256'] and job['release_id']==manifest.release_id and job['activate'] is True
    assert owner.posts==[]


def test_current_active_and_incompatible_channel_do_not_launch(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.discovery import discover
    import naiyue_test_agent.cloud_install.discovery as discovery
    settings,_=inputs(tmp_path);channel(settings);owner=Owner();launched=[]
    original=owner.request
    def request(method,path,**kw):
        if path.startswith('/releases/active/'):return dict(release_id='r2',revision=2)
        return original(method,path,**kw)
    owner.request=request;monkeypatch.setattr(discovery,'launch',lambda *a:launched.append(a))
    assert discover(settings,owner,settings_file=tmp_path/'update.json')['phase']=='up_to_date'
    def incompatible(method,path,**kw):
        if path=='/instance':return dict(instance_id='fixture',protocol=2,version='0.5.6',capabilities=['releases:write'])
        return original(method,path,**kw)
    owner.request=incompatible
    assert discover(settings,owner,settings_file=tmp_path/'update.json')['phase']=='requires_maintenance'
    assert launched==[] and owner.posts==[]


def test_prepare_only_worker_never_activates(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.store import UpdateStore
    from naiyue_test_agent.cloud_install.worker import execute
    from lifecycle_helpers import template
    import naiyue_test_agent.cloud_install.worker as worker
    settings,manifest=inputs(tmp_path);ident=str(uuid4());owner=Owner()
    UpdateStore(settings).create(ident,'r2',activate=False)
    monkeypatch.setattr(worker,'prepare_template',lambda build,r:template(runner_release_id=r,
        wheel_sha256=manifest.wheels[settings.platform].sha256,kit_wheel_sha256=manifest.kit.sha256))
    result=execute(settings,ident,client=owner)
    assert result['phase']=='completed' and result['step']=='ready'
    assert [path for path,_ in owner.posts]==['/releases/register'] and owner.revision==0


def test_cache_budget_fails_before_download_or_build(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.store import UpdateStore
    from naiyue_test_agent.cloud_install.worker import execute
    import naiyue_test_agent.cloud_install.worker as worker
    settings,_=inputs(tmp_path);settings=settings.model_copy(update={'max_cache_bytes':1})
    ident=str(uuid4());UpdateStore(settings).create(ident,'r2');owner=Owner();built=[]
    monkeypatch.setattr(worker,'prepare_template',lambda *a:built.append(a))
    result=execute(settings,ident,client=owner)
    assert result['phase']=='failed' and result['error_code']=='update_cache_budget'
    assert built==[] and owner.posts==[]


def test_discover_is_wired_to_the_real_cli_and_fixed_remote_arguments(tmp_path,monkeypatch,capsys):
    from naiyue_test_agent.cli.cloud import main
    from naiyue_test_agent.cloud_install.remote import RemoteRequest
    import naiyue_test_agent.cloud_install.discovery as discovery
    settings,_=inputs(tmp_path);channel(settings);path=tmp_path/'update.json'
    settings.connection_file.write_text(json.dumps(dict(endpoint='http://127.0.0.1:28101',owner_authority='127.0.0.1:28101',
        owner_token='fixture-only',instance_id=str(uuid4()))))
    path.write_text(settings.model_dump_json());path.chmod(0o600)
    monkeypatch.setattr(discovery,'discover',lambda value,client,**kw:dict(phase='up_to_date',channel=value.channel))
    assert main(['release','discover','--settings',str(path)])==0
    assert json.loads(capsys.readouterr().out)['channel']=='stable'
    assert RemoteRequest(action='discover').arguments()==['discover']


def test_inventory_generation_rebuilds_a_previously_completed_prepare_job(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.discovery import discover
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cloud_install.discovery as module
    settings,_=inputs(tmp_path);channel(settings);settings=settings.model_copy(update={'auto_activate':False})
    owner=Owner();original=owner.request;epoch=[0]
    def request(method,path,**kw):
        if path.startswith('/releases/active/'):return dict(release_id=None,revision=0,inventory_revision=epoch[0])
        return original(method,path,**kw)
    owner.request=request
    monkeypatch.setattr(module,'launch',lambda path,ident:UpdateStore(settings).save(ident,phase='completed',step='ready'))
    first=discover(settings,owner,settings_file=tmp_path/'config.json')
    epoch[0]+=1
    second=discover(settings,owner,settings_file=tmp_path/'config.json')
    assert first['command_id']!=second['command_id']
    assert UpdateStore(settings).read(first['command_id'])==first
