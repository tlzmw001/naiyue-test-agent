"""The existing detached worker and fixed SSH CLI also own offline operations."""
from uuid import uuid4
from test_instance_backup import fixture


def test_offline_backup_is_executed_by_original_worker_without_owner_http(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.worker import execute
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cloud_install.worker as worker
    settings,_,_,_=fixture(tmp_path,monkeypatch);ident=str(uuid4());store=UpdateStore(settings)
    store.create_operation(ident,'backup',{})
    def forbidden(*a,**kw):raise AssertionError('offline backup opened owner HTTP')
    monkeypatch.setattr(worker,'CloudClient',forbidden)
    result=execute(settings,ident)
    assert result['phase']=='completed' and result['backup_id']==ident
    assert execute(settings,ident)==result


def test_fixed_remote_arguments_cover_backup_restore_and_confirmation():
    from naiyue_test_agent.cloud_install.remote import RemoteRequest
    ident=str(uuid4());backup=str(uuid4())
    assert RemoteRequest(action='backups').arguments()==['backups']
    assert RemoteRequest(action='backup',command_id=ident).arguments()==['backup','--command-id',ident]
    assert RemoteRequest(action='restore',command_id=ident,backup_id=backup,confirm=backup).arguments()==[
        'restore',backup,'--command-id',ident,'--confirm',backup]
    assert RemoteRequest(action='restore-activate',command_id=ident,confirm=ident).arguments()==['restore-activate',ident,'--confirm',ident]


def test_gc_cli_confirmation_updates_the_original_job_and_launches_same_id(tmp_path,monkeypatch):
    from argparse import Namespace
    from naiyue_test_agent.cli.cloud_release import execute
    from naiyue_test_agent.cloud_install.store import UpdateStore
    import naiyue_test_agent.cli.cloud_release as module
    settings,_,_,_=fixture(tmp_path,monkeypatch);store=UpdateStore(settings);ident=str(uuid4());sha='c'*64
    store.create_operation(ident,'gc',{});store.save(ident,phase='prepared',plan_sha256=sha)
    monkeypatch.setattr(module,'read_settings',lambda *a:settings);calls=[]
    monkeypatch.setattr(module,'launch',lambda path,cid:calls.append(cid) or store.read(cid))
    value=execute(Namespace(release_command='gc',command_id=ident,confirm=sha,remote=None,settings=tmp_path/'settings.json'))
    assert value['approved_digest']==sha and value['phase']=='queued' and calls==[ident]
