"""Preview, confirm and resume bounded offline GC without a global Docker prune."""
import json,re,os,logging
from pathlib import Path

from naiyue_test_agent.contracts.cloud_tasks import CloudError,TemplateReceipt
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.raw_tree import tree_inventory,remove_tree
from naiyue_test_agent.storage.cloud.guards import read_guard
from .store import directory,read,write
from .offline import offline
from .upgrade import _run
from .reclaim_refs import references

logger=logging.getLogger(__name__)


def _image(settings,value,*,missing=False):
    rows=[json.loads(line) for line in _run(settings,['image','ls','--all','--no-trunc','--filter',
        'label=org.naiyue.build-input='+value['build_input_sha256'],'--format','{{json .}}']).splitlines() if line.strip()]
    ids=[]
    for row in rows:
        ident=row.get('ID','').removeprefix('sha256:')
        if not re.fullmatch('[a-f0-9]{64}',ident):raise CloudError('gc_image_listing_invalid')
        ids.append('sha256:'+ident)
    if value['image_id'] not in ids:
        if missing:return None
        raise CloudError('gc_image_unproven')
    info=json.loads(_run(settings,['image','inspect',value['image_id']]))[0]
    tag=settings.build.image_tag+'-'+value['command_id'].replace('-','')
    if (info.get('Id')!=value['image_id'] or info.get('Config',{}).get('Labels',{}).get('org.naiyue.build-input')!=value['build_input_sha256']
            or info.get('RepoTags')!=[tag]):raise CloudError('gc_image_unproven')
    if _run(settings,['container','ls','--all','--no-trunc','--filter','ancestor='+value['image_id'],'--format','{{json .}}']).strip():
        raise CloudError('gc_image_referenced')
    return dict(image_id=value['image_id'],tag=tag,build_input_sha256=value['build_input_sha256'])


def _plan(settings,owned,ident):
    refs=references(settings,owned,ident);candidates=[];retire=[]
    protected=[dict(kind='upgrade_backup',name=key,reason='unresolved_reference') for key in refs['unresolved']]
    for key,value in sorted(owned.snapshot['meta'].items()):
        if len(candidates)+len(protected)>=settings.gc_max_resources:raise CloudError('gc_candidate_budget')
        if not key.startswith('release:'):continue
        t=TemplateReceipt.model_validate(value['template'])
        if refs['keep_all'] or key in refs['keys'] or t.image_id in refs['images']:
            protected.append(dict(kind='image',name=t.image_id,reason='task_or_active_or_backup'));continue
        proofs=refs['updates'].get(t.image_id,[])
        proof=None
        for job in proofs:
            if not re.fullmatch('[a-f0-9]{64}',job.get('build_input_sha256','')):continue
            try:
                receipt=TemplateReceipt.model_validate(read(settings.work_dir/job['command_id']/'receipt.json'))
                if receipt.fingerprint!=t.fingerprint:continue
                observed=_image(settings,job);proof=job;break
            except FileNotFoundError:continue
            except CloudError as error:
                if error.code not in ('gc_image_unproven','gc_image_referenced'):raise
        if proof is None:protected.append(dict(kind='image',name=t.image_id,reason='image_not_owned_or_used'));continue
        candidates.append(dict(kind='image',name=t.image_id,proof=proof,observed=observed,release_key=key));retire.append(key)
    cache=directory(settings.work_dir/'artifacts')
    for path in sorted(cache.iterdir()):
        if len(candidates)+len(protected)>=settings.gc_max_resources:raise CloudError('gc_candidate_budget')
        if refs['keep_all'] or path.name in refs['hashes'] or not re.fullmatch('[a-f0-9]{64}',path.name):
            protected.append(dict(kind='cache',name=path.name,reason='referenced_or_unproven'));continue
        candidates.append(dict(kind='cache',name=path.name))
    for backup in refs['backups']:
        if len(candidates)+len(protected)>=settings.gc_max_resources:raise CloudError('gc_candidate_budget')
        if backup['phase']!='complete' or backup['backup_id'] in refs['retained_backups']:
            protected.append(dict(kind='backup',name=backup['backup_id'],reason='backup_integrity_unconfirmed' if backup.get('integrity')!='verified' else 'retention_or_restore_or_incomplete'));continue
        candidates.append(dict(kind='backup',name=backup['backup_id']))
    if len(candidates)>settings.gc_max_resources:raise CloudError('gc_candidate_budget')
    files=0
    for value in candidates:
        if value['kind']=='image':continue
        path=_path(settings,value);inventory=tree_inventory(path,max_files=settings.backup_max_files-files)
        files+=len(inventory);value.update(identity=[path.stat().st_dev,path.stat().st_ino],inventory=inventory,bytes=sum(v.get('size',0) for v in inventory))
    return dict(command_id=ident,snapshot_sha256=digest(owned.snapshot),external_sha256=digest(refs['external']),
        candidates=candidates,protected=protected,retire=retire)


def _path(settings,value):
    if value['kind']=='cache' and re.fullmatch('[a-f0-9]{64}',value['name']):return settings.work_dir/'artifacts'/value['name']
    if value['kind']=='backup':
        from .backup import backup_path
        return backup_path(settings,value['name'])
    raise CloudError('gc_invalid_candidate')


def prepare_gc(settings,ident,store):
    with offline(settings) as owned:
        store._available()
        plan=_plan(settings,owned,ident);path=directory(settings.work_dir/ident)/'gc-plan.json';write(path,plan)
        summaries=[{key:value[key] for key in ('kind','name','bytes') if key in value} for value in plan['candidates']]
        return store.save(ident,phase='prepared',step='awaiting_confirmation',plan_sha256=digest(plan),candidates=summaries,protected=plan['protected'])


def _finish_guards(settings,root,ident):
    for path in (settings.work_dir/'gc-guard.json',root/'gc-guard.json'):
        try:value=read(path)
        except FileNotFoundError:continue
        if value.get('command_id')==ident:write(path,value|dict(phase='completed'))


def apply_gc(settings,ident,store):
    value=store.read(ident);store.require_target(value)
    plan=read(settings.work_dir/ident/'gc-plan.json',settings.backup_max_files*2048+1024*1024)
    sha=digest(plan)
    if sha!=value.get('plan_sha256') or value.get('approved_digest')!=sha or plan.get('command_id')!=ident:raise CloudError('gc_confirmation_required')
    with offline(settings,allow_gc=True) as owned:
        work_guard=read_guard(settings.work_dir,'gc-guard.json')
        if work_guard and work_guard.get('phase')!='completed' and (work_guard.get('command_id'),work_guard.get('plan_sha256'))!=(ident,sha):raise CloudError('update_gc_pending')
        guard=read_guard(owned.settings.data_dir,'gc-guard.json')
        if guard and guard.get('phase')!='completed' and (guard.get('command_id'),guard.get('plan_sha256'))!=(ident,sha):raise CloudError('cloud_gc_guard')
        if value.get('step')=='finalizing':
            _finish_guards(settings,owned.settings.data_dir,ident)
            return store.save(ident,phase='completed',step='completed',error_code=None)
        if value.get('step')!='deleting':
            try:
                if digest(_plan(settings,owned,ident))!=sha:raise CloudError('gc_plan_changed')
                owned.repository.enable_guards();owned.owner.enable_guards()
                fence=dict(command_id=ident,plan_sha256=sha,phase='deleting')
                write(settings.work_dir/'gc-guard.json',fence);write(owned.settings.data_dir/'gc-guard.json',fence)
                store.save(ident,step='deleting',phase='running')
            except BaseException:
                try:
                    if store.read(ident).get('step')!='deleting':_finish_guards(settings,owned.settings.data_dir,ident)
                except (CloudError,OSError,ValueError) as secondary:
                    logger.error('cloud_gc_guard_cleanup_failed error_type=%s',type(secondary).__name__)
                raise
        owned.repository.remove_releases(plan['retire'])
        for candidate in plan['candidates']:
            if candidate['kind']=='image':
                if _image(settings,candidate['proof'],missing=True) is not None:
                    _run(settings,['image','rm',candidate['name']])
                    if _image(settings,candidate['proof'],missing=True) is not None:raise CloudError('gc_delete_unconfirmed')
                continue
            path=_path(settings,candidate)
            if not os.path.lexists(path):continue
            inventory=tree_inventory(path,max_files=settings.backup_max_files)
            expected={v['path']:v for v in candidate['inventory']}
            if any(expected.get(v['path'])!=v for v in inventory):raise CloudError('gc_resource_changed')
            remove_tree(path,max_files=settings.backup_max_files,expected=candidate['identity'],expected_inventory=candidate['inventory'])
        store.save(ident,step='finalizing')
        _finish_guards(settings,owned.settings.data_dir,ident)
        return store.save(ident,phase='completed',step='completed',error_code=None)
