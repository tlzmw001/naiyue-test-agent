"""Whole-instance offline backups: owned SQLite snapshots and isolated task bytes."""
from contextlib import ExitStack
import os,re,time
from uuid import UUID,uuid4,uuid5

from pydantic import TypeAdapter
from naiyue_test_agent import __version__
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CloudTask,UUIDText,TemplateReceipt
from naiyue_test_agent.contracts.cloud_versions import OWNER_PROTOCOL,RUNNER_PROTOCOL,STATE_SCHEMA
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.cloud.operations import backup_database
from naiyue_test_agent.storage.raw_tree import copy_roots,describe_tree,verify_tree
from naiyue_test_agent.storage.files import check_store_directory,open_store_readonly,sync_store_directory
from .store import directory,write,read
from .offline import offline
from .upgrade import backup_container
from .maintenance import select_maintenance_template


def backup_path(settings,ident):
    ident=TypeAdapter(UUIDText).validate_python(ident)
    return directory(settings.work_dir/'instance-backups')/ident


def list_backups(settings):
    base=directory(settings.work_dir/'instance-backups');result=[]
    for path in sorted(base.iterdir()):
        ident=TypeAdapter(UUIDText).validate_python(path.name);check_store_directory(path)
        try:complete=read(path/'complete.json')
        except FileNotFoundError:result.append(dict(backup_id=ident,phase='incomplete'));continue
        try:
            manifest,_=_manifest(settings,ident)
            result.append(dict(backup_id=ident,phase='complete',integrity='not_checked',created_at=manifest['created_at'],
                instance_id=manifest['instance_id'],bytes=sum(e.get('size',0) for e in manifest['entries']),manifest_sha256=complete['manifest_sha256']))
        except (CloudError,OSError,ValueError):result.append(dict(backup_id=ident,phase='invalid'))
    return result


def _manifest(settings,ident):
    root=backup_path(settings,ident);check_store_directory(root);complete=read(root/'complete.json')
    if complete.get('backup_id')!=ident or not re.fullmatch(r'attempt-[a-f0-9]{32}',complete.get('attempt','')):raise CloudError('backup_invalid')
    attempt=root/complete['attempt'];check_store_directory(attempt)
    manifest=read(attempt/'manifest.json',settings.backup_max_files*1024+1024*1024)
    if (digest(manifest)!=complete.get('manifest_sha256') or manifest.get('backup_id')!=ident
            or manifest.get('schema_version')!=1 or manifest.get('state_schema')!=STATE_SCHEMA
            or manifest.get('coordinator_protocol')!=OWNER_PROTOCOL or manifest.get('runner_protocol')!=RUNNER_PROTOCOL):raise CloudError('backup_invalid')
    return manifest,attempt/'payload'


def verify_backup(settings,ident):
    manifest,payload=_manifest(settings,ident)
    expected=required_templates(dict(tasks=manifest['tasks'],meta=manifest['meta']),
        {key:TemplateReceipt.model_validate(value) for key,value in manifest['bootstrap_templates'].items()},maintenance_template=manifest['maintenance_template'])
    if expected!=manifest['required_templates'] or sorted({v['key'] for v in expected})!=manifest['required_releases']:raise CloudError('backup_invalid')
    verify_tree({'payload':payload},manifest['entries'],max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files,verify_modes=True)
    return manifest,payload


def required_releases(snapshot,templates,*,maintenance_template=None):
    return sorted({v['key'] for v in required_templates(snapshot,templates,maintenance_template=maintenance_template)})


def required_templates(snapshot,templates,*,maintenance_template=None):
    values=list(templates.values())
    if maintenance_template is not None:values.append(TemplateReceipt.model_validate(maintenance_template))
    for value in snapshot['tasks']:
        if value['lifecycle']=='deleted':continue
        task=CloudTask.model_validate(value);t=task.template_snapshot or templates.get(task.template_id)
        if t is None or t.runner_release_id!=task.runner_release_id:raise CloudError('backup_required_release_missing')
        values.append(t)
    for key,value in snapshot['meta'].items():
        if not key.startswith('release-active:'):continue
        record=snapshot['meta'].get('release:'+value['release_id']+':'+value['template_id'])
        if record is None:raise CloudError('backup_required_release_missing')
        values.append(TemplateReceipt.model_validate(record['template']))
    result={}
    for t in values:
        key='release:'+t.runner_release_id+':'+t.template_id
        record=snapshot['meta'].get(key)
        if snapshot['meta'].get('release-retired:'+t.runner_release_id+':'+t.template_id):raise CloudError('backup_required_release_missing')
        if record is not None and TemplateReceipt.model_validate(record['template']).fingerprint!=t.fingerprint:raise CloudError('backup_required_release_missing')
        result[(key,t.fingerprint)]=dict(key=key,fingerprint=t.fingerprint,image_id=t.image_id,template=t.model_dump(mode='json'))
    return [result[key] for key in sorted(result)]


def _management(owned,destination,settings):
    source=owned.settings.data_dir
    allowed={'cloud-root.json','owner.lock','cloud.sqlite','cloud.sqlite.lock','cloud.sqlite-wal','cloud.sqlite-shm','cloud.sqlite-journal',
        'history','inputs','transfers','artifacts','artifact-transfers','tasks','restore-guard.json','gc-guard.json'}
    if set(os.listdir(source))-allowed:raise CloudError('offline_unknown_root_entry')
    write(destination/'cloud-root.json',owned.owner.record|dict(schema=2))
    backup_database(owned.database,destination/'cloud.sqlite',guard_format=True)
    history=directory(destination/'history');known={t['task_id'] for t in owned.snapshot['tasks']}
    for path in sorted((source/'history').iterdir()):
        if not any(path.name==ident+'.sqlite'+suffix for ident in known for suffix in ('','-wal','-shm','.lock','-journal')):
            raise CloudError('offline_unknown_history')
        if path.name.endswith('.sqlite'):
            db=Database.open_cloud(path,profile='history',initialize=False)
            try:backup_database(db,history/path.name)
            finally:db.close()
    roots=[]
    for name in ('inputs','transfers','artifacts','artifact-transfers'):
        if (source/name).exists():roots.append((name,source/name,directory(destination/name)))
    if roots:copy_roots(roots,max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files)


def create_backup(settings,ident):
    root=directory(backup_path(settings,ident))
    if (root/'complete.json').exists():
        manifest,_=verify_backup(settings,ident)
        return dict(backup_id=ident,phase='completed',manifest_sha256=digest(manifest))
    pending=None
    try:pending=read(root/'pending.json')['result']
    except FileNotFoundError:pass
    with offline(settings,maintenance_result=pending) as owned:
        attempts=[p for p in root.iterdir() if p.name.startswith('attempt-')]
        if len(attempts)>=3:raise CloudError('backup_attempt_budget')
        attempt=directory(root/('attempt-'+uuid4().hex));payload=directory(attempt/'payload')
        management=directory(payload/'management');_management(owned,management,settings)
        tasks=directory(payload/'task-backups');markers={};backups={}
        parent=owned.settings.data_dir/'tasks'
        helper=(select_maintenance_template(settings,owned) if any(t['lifecycle']!='deleted' and (parent/t['task_id']).exists() for t in owned.snapshot['tasks']) else None)
        known={t['task_id'] for t in owned.snapshot['tasks'] if t['lifecycle']!='deleted'}
        if parent.exists():
            check_store_directory(parent)
            if set(os.listdir(parent))-known:raise CloudError('offline_unknown_task_directory')
        used=sum(e.get('size',0) for e in describe_tree({'management':management},max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files))
        for value in owned.snapshot['tasks']:
            task=CloudTask.model_validate(value);path=parent/task.task_id
            if task.lifecycle=='deleted' or not path.exists():continue
            check_store_directory(path)
            if set(os.listdir(path))-{'workspace','state','environment.json','control'}:raise CloudError('offline_unknown_task_entry')
            marker=read(path/'environment.json')
            if marker!=dict(task_id=task.task_id,project_id=task.project_id,input_id=task.input_id,status='ready'):raise CloudError('upgrade_directory_identity_mismatch')
            template=task.template_snapshot or owned.settings.templates.get(task.template_id)
            if template is None:raise CloudError('template_unavailable')
            reservation=dict(command_id=str(uuid5(UUID(ident),task.task_id)),instance_id=owned.instance_id,task_id=task.task_id,
                project_id=task.project_id,input_id=task.input_id,generation=task.generation,backup_id=ident)
            reservation['source_template_digest']=template.fingerprint
            result=dict(reservation=reservation,target_template=helper.model_dump())
            write(root/'pending.json',dict(result=result))
            remaining=settings.backup_max_bytes-used
            if remaining<=0:raise CloudError('upgrade_backup_budget')
            receipt=backup_container(settings.model_copy(update={'backup_max_bytes':remaining}),result,directory(tasks/task.task_id),owned.instance_id)
            used+=receipt['bytes'];markers[task.task_id]=marker;backups[task.task_id]=dict(result=result,receipt=receipt)
        from .offline import ensure_no_writers
        ensure_no_writers(settings,owned.instance_id)
        entries=describe_tree({'payload':payload},max_bytes=settings.backup_max_bytes,max_files=settings.backup_max_files)
        manifest=dict(schema_version=1,backup_id=ident,created_at=time.time(),instance_id=owned.instance_id,
            source_root=str(owned.settings.data_dir),source_root_identity=[owned.settings.data_dir.stat().st_dev,owned.settings.data_dir.stat().st_ino],
            agent_version=__version__,coordinator_protocol=OWNER_PROTOCOL,
            runner_protocol=RUNNER_PROTOCOL,state_schema=STATE_SCHEMA,tasks=owned.snapshot['tasks'],
            meta=owned.snapshot['meta'],results=owned.snapshot['results'],required_releases=required_releases(owned.snapshot,owned.settings.templates,maintenance_template=helper),
            required_templates=required_templates(owned.snapshot,owned.settings.templates,maintenance_template=helper),
            maintenance_template=helper.model_dump(mode='json') if helper else None,
            bootstrap_templates={key:value.model_dump(mode='json') for key,value in owned.settings.templates.items()},
            markers=markers,task_backups=backups,entries=entries)
        write(attempt/'manifest.json',manifest)
        write(root/'complete.json',dict(backup_id=ident,attempt=attempt.name,manifest_sha256=digest(manifest)))
        return dict(backup_id=ident,phase='completed',manifest_sha256=digest(manifest))
