"""Operator-side isolated raw backup and existing lifecycle-based task restart."""
import json,logging,os,subprocess,time
from uuid import UUID,uuid5

from naiyue_test_agent.contracts.cloud_tasks import CloudError,TemplateReceipt
from naiyue_test_agent.contracts.cloud_releases import BackupRequest,UpgradeBackup
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.files import check_store_directory
from .store import directory,read

logger=logging.getLogger(__name__)


def _run(settings,args):
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','TEMP','SYSTEMROOT','SSL_CERT_FILE','SSL_CERT_DIR')}
    try:
        result=subprocess.run([settings.build.docker_executable,'--host',settings.build.docker_host,*args],
            stdin=subprocess.DEVNULL,capture_output=True,timeout=settings.build.build_timeout_seconds,env=env)
    except subprocess.TimeoutExpired:raise CloudError('upgrade_backup_timeout') from None
    if result.returncode or len(result.stdout)>1024*1024:raise CloudError('upgrade_backup_failed')
    return result.stdout


def backup_container(settings,result,backup_dir,instance_id):
    reservation=result['reservation'];task_id=reservation['task_id'];template=TemplateReceipt.model_validate(result['target_template'])
    if settings.task_data_dir is None:raise CloudError('upgrade_data_root_required')
    root=settings.task_data_dir;check_store_directory(root)
    if read(root/'cloud-root.json').get('instance_id')!=instance_id:raise CloudError('cloud_instance_mismatch')
    if (os.getuid(),os.getgid())!=(template.uid,template.gid):raise CloudError('upgrade_directory_identity_mismatch')
    task=root/'tasks'/task_id;check_store_directory(task)
    marker=read(task/'environment.json')
    if (marker.get('task_id')!=task_id or marker.get('status')!='ready'
            or any(marker.get(k)!=reservation[k] for k in ('project_id','input_id') if k in reservation)):
        raise CloudError('upgrade_directory_identity_mismatch')
    for name in ('workspace','state'):check_store_directory(task/name)
    info=json.loads(_run(settings,['info','--format','{{json .}}']))
    if info.get('ID')!=template.backend_id:raise CloudError('backend_identity_mismatch')
    image=json.loads(_run(settings,['image','inspect',template.image_id]))[0]
    if image.get('Id')!=template.image_id:raise CloudError('template_cached_identity_mismatch')
    request=BackupRequest(command_id=reservation['command_id'],task_id=task_id,generation=reservation['generation'],reservation_sha256=digest(reservation))
    name,labels=container_identity(result)
    reconcile_backup(settings,result)
    args=['run','--rm','--name',name,'--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--user',f'{template.uid}:{template.gid}','--memory',settings.backup_memory,
        '--cpus',str(settings.backup_cpus),'--pids-limit',str(settings.backup_pids)]
    for key,value in labels.items():args+=['--label',key+'='+value]
    for source,target,readonly in [(task/'workspace','/workspace',True),(task/'state','/state',True),(backup_dir,'/backup',False)]:
        # Docker --mount CSV cannot represent commas in host paths without a separate encoding contract.
        if ',' in str(source):raise CloudError('upgrade_mount_path_invalid')
        args+=['--mount','type=bind,src='+str(source)+',dst='+target+(',readonly' if readonly else '')]
    args += [template.image_id,'/opt/nyt/runner/bin/python','-I','-m','naiyue_test_agent.cloud_runner.upgrade_backup',
        '--request',request.model_dump_json(),'--max-bytes',str(settings.backup_max_bytes),'--max-files',str(settings.backup_max_files)]
    try:raw=_run(settings,args)
    except CloudError as primary:
        try:reconcile_backup(settings,result)
        except CloudError:raise CloudError('upgrade_backup_unknown') from primary
        raise
    reconcile_backup(settings,result)
    backup=UpgradeBackup.model_validate_json(raw)
    if any(getattr(backup,k)!=getattr(request,k) for k in type(request).model_fields):raise CloudError('upgrade_backup_identity_mismatch')
    return backup.model_dump()


def execute_upgrade(settings,ident,store,client):
    value=store.read(ident);task_id=value['task_id']
    request=value.get('upgrade_request')
    if request is None:
        task=client.request('GET','/tasks/'+task_id)
        request=dict(command_id=ident,release_id=value['release_id'],expected_revision=task['revision'],expected_generation=task['generation'])
        store.save(ident,upgrade_request=request)
    store.save(ident,phase='running',step='reserving_upgrade',error_code=None)
    result=client.request('POST','/tasks/'+task_id+'/upgrade',json=request)
    reservation=result['reservation']
    if reservation['instance_id']!=client.settings.instance_id or value.get('instance_id',reservation['instance_id'])!=reservation['instance_id']:
        raise CloudError('cloud_instance_mismatch')
    store.save(ident,instance_id=reservation['instance_id'])
    if result['phase']=='cancelled':return store.save(ident,phase='cancelled',step='cancelled')
    if result['phase']!='committed':
        if result['phase']!='verified':raise CloudError('upgrade_not_prepared')
        store.save(ident,step='backing_up')
        destination=directory(directory(settings.work_dir/'backups')/ident)
        backup=backup_container(settings,result,destination,client.settings.instance_id)
        store.save(ident,backup=backup,step='committing_upgrade')
        result=client.request('POST','/tasks/'+task_id+'/upgrade/commit',json=backup)
    store.save(ident,step='starting_upgrade',backup=result['backup'])
    value=store.read(ident);start=value.get('start_request')
    if start is None:
        task=client.request('GET','/tasks/'+task_id)
        start=dict(command_id=str(uuid5(UUID(ident),'start')),kind='start',expected_revision=task['revision'],expected_generation=task['generation'])
        store.save(ident,start_request=start)
    receipt=client.request('POST','/tasks/'+task_id+'/commands',json=start)
    if receipt['status'] in ('unknown','running','accepted'):
        receipt=client.request('GET','/commands/'+start['command_id'])
        if receipt['status'] in ('unknown','running','accepted'):
            receipt=client.request('POST','/commands/'+start['command_id']+'/reconcile',json={})
    if receipt['status']!='succeeded':raise CloudError('upgrade_start_unconfirmed')
    store.save(ident,step='checking_runner');deadline=time.monotonic()+settings.request_timeout_seconds
    while True:
        task=client.request('GET','/tasks/'+task_id)
        if task['runner_release_id']!=value['release_id'] or task['generation']!=request['expected_generation']+1:
            raise CloudError('upgrade_runner_identity_mismatch')
        if task['runner_phase'] in ('ready','waiting_model'):
            return store.save(ident,phase='completed',step='completed',runner_phase=task['runner_phase'],generation=task['generation'])
        if task['runner_phase']=='blocked':
            return store.save(ident,phase='requires_recovery',step='checking_runner',error_code='upgrade_runner_requires_recovery')
        if time.monotonic()>=deadline:raise CloudError('upgrade_runner_not_ready')
        time.sleep(min(.25,max(0,deadline-time.monotonic())))


def container_identity(result):
    r=result['reservation']
    return 'nyt-upgrade-'+r['command_id'].replace('-',''),{
        'org.naiyue.upgrade-command':r['command_id'],'org.naiyue.upgrade-task':r['task_id'],
        'org.naiyue.upgrade-instance':r['instance_id'],'org.naiyue.upgrade-reservation':digest(r)}


def inspect_backup(settings,result):
    name,labels=container_identity(result)
    raw=_run(settings,['container','ls','--all','--no-trunc','--filter','name=^/'+name+'$','--format','{{json .}}'])
    rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
    if not rows:return None
    if len(rows)!=1:raise CloudError('upgrade_backup_unknown')
    import re
    ident=rows[0].get('ID')
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{64}',ident):raise CloudError('upgrade_backup_unknown')
    observed=json.loads(_run(settings,['container','inspect',ident]))[0]
    actual=observed.get('Config',{}).get('Labels') or {}
    if (observed.get('Id')!=ident or observed.get('Name')!='/'+name
            or observed.get('Image')!=result['target_template']['image_id']
            or any(actual.get(k)!=v for k,v in labels.items())):raise CloudError('upgrade_backup_container_conflict')
    if type(observed.get('State',{}).get('Running')) is not bool:raise CloudError('upgrade_backup_unknown')
    return observed


def reconcile_backup(settings,result):
    observed=inspect_backup(settings,result)
    if observed is None:return None
    ident=observed['Id']
    if observed['State']['Running']:
        try:_run(settings,['kill',ident])
        except CloudError:
            # A lost stop ACK must be resolved through fresh daemon facts.
            logger.warning('cloud_upgrade_backup_stop_unconfirmed')
        observed=inspect_backup(settings,result)
        if observed is not None and observed['State']['Running']:raise CloudError('upgrade_backup_unknown')
    if observed is not None:
        try:_run(settings,['rm',ident])
        except CloudError:logger.warning('cloud_upgrade_backup_remove_unconfirmed')
        if inspect_backup(settings,result) is not None:raise CloudError('upgrade_backup_unknown')
    return ident


def cancel_pending(settings,ident,store,client):
    value=store.read(ident);store.require_target(value)
    if not value.get('task_id'):raise CloudError('upgrade_command_required')
    receipt=client.request('GET','/commands/'+ident);result=receipt['result'];r=result['reservation']
    if result['phase']=='committed':raise CloudError('upgrade_already_committed')
    if r['instance_id']!=client.settings.instance_id:raise CloudError('cloud_instance_mismatch')
    info=json.loads(_run(settings,['info','--format','{{json .}}']))
    if info.get('ID')!=result['target_template']['backend_id']:raise CloudError('backend_identity_mismatch')
    try:observed=reconcile_backup(settings,result)
    except CloudError as error:raise CloudError('upgrade_backup_unknown') from error
    name,_=container_identity(result)
    request=dict(command_id=ident,task_id=value['task_id'],generation=r['generation'],reservation_sha256=digest(r),
        container_name=name,observed_container_id=observed)
    result=client.request('POST','/tasks/'+value['task_id']+'/upgrade/cancel',json=request)
    store.save(ident,phase='cancelled',step='cancelled')
    return result
