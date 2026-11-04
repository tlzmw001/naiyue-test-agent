import json,os
from types import SimpleNamespace
from uuid import uuid4,UUID,uuid5
import pytest
from test_update_installer import inputs
from lifecycle_helpers import template
from naiyue_test_agent.cloud_install.store import UpdateStore,directory,write
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_events import digest


def test_unknown_start_is_reconciled_under_original_identity(tmp_path):
    from naiyue_test_agent.cloud_install.upgrade import execute_upgrade
    settings,_=inputs(tmp_path);store=UpdateStore(settings);ident=str(uuid4());task='a'*32
    store.create(ident,'r2',task_id=task)
    start_id=str(uuid5(UUID(ident),'start'))
    store.save(ident,upgrade_request=dict(command_id=ident,release_id='r2',expected_revision=1,expected_generation=1),
        start_request=dict(command_id=start_id,kind='start',expected_revision=3,expected_generation=1))
    class Owner:
        def __init__(self):self.calls=[];self.ready=False;self.settings=SimpleNamespace(instance_id='fixture')
        def request(self,method,path,**kw):
            self.calls.append((method,path,kw.get('json')))
            if path.endswith('/upgrade'):return dict(phase='committed',backup={},reservation={'instance_id':'fixture'})
            if path.endswith('/reconcile'):self.ready=True;return dict(status='succeeded',command_id=start_id)
            if path.endswith('/commands') or path=='/commands/'+start_id:return dict(status='unknown',command_id=start_id)
            return dict(runner_release_id='r2',generation=2,runner_phase='ready' if self.ready else 'booting')
    owner=Owner();result=execute_upgrade(settings,ident,store,owner)
    assert result['phase']=='completed'
    assert ('POST','/commands/'+start_id+'/reconcile',{}) in owner.calls
    assert all(body['command_id']==start_id for method,path,body in owner.calls if path.endswith('/commands'))


def backup_state(tmp_path):
    settings,_=inputs(tmp_path);root=directory(tmp_path/'cloud');task_id='a'*32;instance=str(uuid4());ident=str(uuid4())
    write(root/'cloud-root.json',dict(instance_id=instance));task=directory(directory(root/'tasks')/task_id)
    write(task/'environment.json',dict(task_id=task_id,status='ready'));directory(task/'workspace');directory(task/'state')
    reservation=dict(command_id=ident,task_id=task_id,generation=1,instance_id=instance)
    result=dict(reservation=reservation,target_template=template(runner_release_id='r2').model_dump())
    return settings.model_copy(update={'task_data_dir':root}),result,directory(tmp_path/'backup'),instance


def test_timeout_confirms_original_backup_container_stopped_before_returning(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_install.upgrade as module
    settings,result,dest,instance=backup_state(tmp_path);state=dict(running=False,created=False);calls=[]
    labels={'org.naiyue.upgrade-command':result['reservation']['command_id'],'org.naiyue.upgrade-task':'a'*32,
        'org.naiyue.upgrade-instance':instance,'org.naiyue.upgrade-reservation':digest(result['reservation'])}
    cid='c'*64;name='nyt-upgrade-'+result['reservation']['command_id'].replace('-','')
    def run(settings,args):
        calls.append(args)
        if args[0]=='info':return json.dumps(dict(ID='fixture-daemon')).encode()
        if args[:2]==['image','inspect']:return json.dumps([dict(Id=template().image_id)]).encode()
        if args[:2]==['container','ls']:return (json.dumps(dict(ID=cid)) if state['created'] else '').encode()
        if args[:2]==['container','inspect']:return json.dumps([dict(Id=cid,Name='/'+name,Image=template().image_id,Config=dict(Labels=labels),State=dict(Running=state['running']))]).encode()
        if args[0]=='run':state.update(running=True,created=True);raise CloudError('upgrade_backup_timeout')
        if args[0]=='kill':state['running']=False;return b''
        if args[0]=='rm':state['created']=False;return b''
        raise AssertionError(args)
    monkeypatch.setattr(module,'_run',run)
    with pytest.raises(CloudError,match='upgrade_backup_timeout'):module.backup_container(settings,result,dest,instance)
    assert not state['running'] and not state['created']
    assert any(args[0]=='kill' for args in calls)


def test_cancel_retains_reservation_until_owned_container_exit_is_confirmed(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_install.upgrade as module
    settings,result,dest,instance=backup_state(tmp_path);r=result['reservation'];ident=r['command_id'];store=UpdateStore(settings)
    store.create(ident,'r2',task_id=r['task_id']);name,labels=module.container_identity(result);cid='c'*64;state={'exists':True,'running':True};posts=[]
    class Owner:
        settings=SimpleNamespace(instance_id=instance)
        def request(self,method,path,**kw):
            if method=='GET':return dict(result=result|dict(phase='verified'))
            posts.append(kw['json']);return dict(phase='cancelled')
    def run(settings,args):
        if args[0]=='info':return json.dumps(dict(ID='fixture-daemon')).encode()
        if args[:2]==['container','ls']:return (json.dumps(dict(ID=cid)) if state['exists'] else '').encode()
        if args[:2]==['container','inspect']:return json.dumps([dict(Id=cid,Name='/'+name,Image=template().image_id,
            Config=dict(Labels=labels),State=dict(Running=state['running']))]).encode()
        if args[0]=='kill':raise CloudError('upgrade_backup_timeout')
        if args[0]=='rm':state['exists']=False;return b''
        raise AssertionError(args)
    monkeypatch.setattr(module,'_run',run)
    with pytest.raises(CloudError,match='upgrade_backup_unknown'):module.cancel_pending(settings,ident,store,Owner())
    assert posts==[] and store.read(ident)['phase']=='queued'
    state['running']=False
    assert module.cancel_pending(settings,ident,store,Owner())['phase']=='cancelled'
    assert len(posts)==1 and posts[0]['reservation_sha256']==digest(r)


def test_same_name_container_with_other_reservation_is_never_killed(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_install.upgrade as module
    settings,result,_,_=backup_state(tmp_path);name,labels=module.container_identity(result);labels['org.naiyue.upgrade-reservation']='e'*64;calls=[]
    def run(settings,args):
        calls.append(args)
        if args[:2]==['container','ls']:return json.dumps(dict(ID='c'*64)).encode()
        if args[:2]==['container','inspect']:return json.dumps([dict(Id='c'*64,Name='/'+name,Image=template().image_id,
            Config=dict(Labels=labels),State=dict(Running=True))]).encode()
        raise AssertionError('foreign container mutated')
    monkeypatch.setattr(module,'_run',run)
    with pytest.raises(CloudError,match='upgrade_backup_container_conflict'):module.reconcile_backup(settings,result)
    assert not any(args[0] in ('kill','rm','run') for args in calls)
