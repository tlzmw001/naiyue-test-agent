"""Prepare a verified offline new root; explicit activation fences the old root first."""
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID,uuid4,uuid5
import hashlib,json,os

from naiyue_test_agent.contracts.cloud_tasks import CloudError,TemplateReceipt
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.cloud.operations import OperationRepository
from naiyue_test_agent.storage.cloud.guards import read_guard
from naiyue_test_agent.storage.raw_tree import copy_roots,describe_tree,verify_tree
from naiyue_test_agent.storage.files import check_store_directory,check_store_file
from naiyue_test_agent.application.cloud.ownership import CloudOwner
from naiyue_test_agent.application.cloud.releases import CloudReleases
from .store import directory,read,write,UpdateStore
from .offline import offline
from .backup import verify_backup
from .upgrade import _run,container_identity,reconcile_backup


def restore_container(settings,result,source,destination,request_file):
    template=TemplateReceipt.model_validate(result['target_template'])
    if (os.getuid(),os.getgid())!=(template.uid,template.gid) or template.backend_id!=settings.build.backend_id:
        raise CloudError('upgrade_directory_identity_mismatch')
    info=json.loads(_run(settings,['image','inspect',template.image_id]))[0]
    if info.get('Id')!=template.image_id:raise CloudError('template_cached_identity_mismatch')
    name,labels=container_identity(result);reconcile_backup(settings,result)
    args=['run','--rm','--name',name,'--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{template.uid}:{template.gid}','--memory',settings.backup_memory,'--cpus',str(settings.backup_cpus),'--pids-limit',str(settings.backup_pids)]
    for key,value in labels.items():args+=['--label',key+'='+value]
    for path,target,readonly in ((source,'/backup',True),(destination/'workspace','/workspace',False),
            (destination/'state','/state',False),(request_file,'/request.json',True)):
        if ',' in str(path):raise CloudError('upgrade_mount_path_invalid')
        if target=='/request.json':check_store_directory(path.parent);check_store_file(path)
        else:check_store_directory(path)
        args+=['--mount','type=bind,src='+str(path)+',dst='+target+(',readonly' if readonly else '')]
    args += [template.image_id,'/opt/nyt/runner/bin/python','-I','-m','naiyue_test_agent.cloud_runner.restore_backup',
        '--max-bytes',str(settings.backup_max_bytes),'--max-files',str(settings.backup_max_files)]
    try:value=json.loads(_run(settings,args))
    except (CloudError,ValueError) as primary:
        try:reconcile_backup(settings,result)
        except CloudError:raise CloudError('upgrade_backup_unknown') from primary
        raise
    reconcile_backup(settings,result)
    if value.get('task_id')!=result['reservation']['task_id'] or value.get('regenerable_results_reset') is not True:
        raise CloudError('restore_task_identity_mismatch')
    return value


def _record(settings,ident,store):
    value=store.read(ident);store.require_target(value)
    if value.get('operation')!='restore':raise CloudError('command_id_conflict')
    path=settings.work_dir/ident/'prepared.json';record=read(path,settings.backup_max_files*1024+1024*1024)
    if digest(record)!=value.get('prepared_sha256') or record.get('command_id')!=ident:raise CloudError('restore_target_invalid')
    root=Path(record['restore_root'])
    if settings.recovery_dir is None or root.parent!=settings.recovery_dir/ident or not root.name.startswith('attempt-'):
        raise CloudError('restore_target_invalid')
    check_store_directory(root)
    if [root.stat().st_dev,root.stat().st_ino]!=record['root_identity']:raise CloudError('restore_target_invalid')
    return value,record,root


def prepare_restore(settings,ident,backup_id,store):
    value=store.read(ident)
    if value['phase'] in ('prepared','completed'):
        _record(settings,ident,store);return value
    if settings.recovery_dir is None:raise CloudError('restore_directory_required')
    with offline(settings,maintenance_result=value.get('pending_result')) as owned:
        manifest,payload=verify_backup(settings,backup_id)
        if manifest['instance_id']!=owned.instance_id or manifest['source_root']!=str(owned.settings.data_dir):raise CloudError('cloud_instance_mismatch')
        parent=directory(directory(settings.recovery_dir)/ident)
        if len(list(parent.iterdir()))>=3:raise CloudError('restore_attempt_budget')
        root=directory(parent/('attempt-'+uuid4().hex));jobdir=directory(settings.work_dir/ident)
        binding=dict(command_id=ident,backup_id=backup_id,instance_id=owned.instance_id,source_root=str(owned.settings.data_dir),
            source_root_identity=[owned.settings.data_dir.stat().st_dev,owned.settings.data_dir.stat().st_ino],
            restore_root=str(root),root_identity=[root.stat().st_dev,root.stat().st_ino])
        write(root/'restore-guard.json',binding|dict(phase='prepared'))
        store.save(ident,phase='running',step='restoring_management',restore_root=str(root))
        copy_roots([('management',payload/'management',root)],max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files)
        tasks=directory(root/'tasks')
        for task_id,backup in manifest['task_backups'].items():
            target=directory(tasks/task_id);write(target/'environment.json',manifest['markers'][task_id])
            directory(target/'workspace');directory(target/'state')
            reservation=backup['result']['reservation']|dict(command_id=str(uuid5(UUID(ident),task_id)),restore_id=ident)
            result=dict(reservation=reservation,target_template=backup['result']['target_template'])
            retired={r['result_id']:r['plan_sha256'] for r in manifest['results'] if r['task_id']==task_id and r['phase'] in ('available','abandoned','superseded','deleted')}
            request=jobdir/(task_id+'-request.json');write(request,dict(backup=backup['receipt'],retired_results=retired))
            store.save(ident,pending_result=result,step='restoring_task')
            restore_container(settings,result,payload/'task-backups'/task_id,target,request)
        from .offline import ensure_no_writers
        ensure_no_writers(settings,owned.instance_id)
        target_settings=owned.settings.model_copy(update={'data_dir':root})
        with CloudOwner(target_settings) as target_owner:
            db=Database.open_cloud(root/'cloud.sqlite',profile='registry',initialize=False)
            try:
                OperationRepository(db).rebind_restored(root,retained_releases=manifest['required_releases'])
                runtime=SimpleNamespace(settings=target_settings,database=db,instance_id=target_owner.record['instance_id'],lifecycle=None)
                releases=CloudReleases(runtime);releases.validate_active()
                for required in manifest['required_templates']:
                    if required['key'] in manifest['meta'] and releases.repository.read(required['key']) is None:raise CloudError('backup_required_release_missing')
                    releases.validate_template(required['template'])
                for task in OperationRepository(db).snapshot()['tasks']:
                    if task['template_snapshot'] is not None:releases.validate_template(task['template_snapshot'])
            finally:db.close()
        entries=describe_tree({'root':root},max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files)
        record=binding|dict(entries=entries,source_snapshot_sha256=digest(owned.snapshot),source_guard=read_guard(owned.settings.data_dir,'restore-guard.json'))
        write(jobdir/'prepared.json',record)
        return store.save(ident,phase='prepared',step='awaiting_activation',prepared_sha256=digest(record),backup_id=backup_id)


def activate_restore(settings,ident,confirmation):
    if confirmation!=ident:raise CloudError('restore_confirmation_required')
    store=UpdateStore(settings);value,record,root=_record(settings,ident,store)
    if value['phase']=='completed':return value
    with offline(settings,allow_restore=True,additional_root=root) as owned:
        if [owned.settings.data_dir.stat().st_dev,owned.settings.data_dir.stat().st_ino]!=record['source_root_identity']:
            raise CloudError('restore_source_changed')
        binding={k:record[k] for k in ('command_id','backup_id','instance_id','source_root','source_root_identity','restore_root','root_identity')}
        fence=binding|dict(phase='superseded');old_guard=read_guard(owned.settings.data_dir,'restore-guard.json')
        if old_guard not in (record['source_guard'],fence):raise CloudError('restore_source_already_superseded')
        if digest(owned.snapshot)!=record['source_snapshot_sha256']:raise CloudError('restore_source_changed')
        target_guard=read_guard(root,'restore-guard.json')
        if target_guard not in (binding|dict(phase='prepared'),binding|dict(phase='active')):raise CloudError('restore_target_invalid')
        entries=record['entries']
        if target_guard['phase']=='active':
            raw=json.dumps(target_guard,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
            entries=[e|dict(size=len(raw),sha256=hashlib.sha256(raw).hexdigest()) if e['path']=='root/restore-guard.json' else e for e in entries]
        verify_tree({'root':root},entries,max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files,verify_modes=True)
        owned.repository.enable_guards();owned.owner.enable_guards()
        write(owned.settings.data_dir/'restore-guard.json',fence)
        write(root/'restore-guard.json',binding|dict(phase='active'))
        return store.save(ident,phase='completed',step='activated',error_code=None)
