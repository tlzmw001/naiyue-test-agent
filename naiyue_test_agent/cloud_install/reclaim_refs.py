"""Resolve current execution and retained backup references, not historical receipts."""
import re,logging
from .store import read,directory,UpdateStore
from .backup import list_backups,_manifest,required_templates,verify_backup
from .source import load_index,load_release,cached_release
from naiyue_test_agent.contracts.cloud_tasks import TemplateReceipt
from naiyue_test_agent.contracts.cloud_tasks import CloudError

logger=logging.getLogger(__name__)


def references(settings,owned,ident):
    needed={v['key'] for v in required_templates(owned.snapshot,owned.settings.templates)}
    images={v['image_id'] for v in required_templates(owned.snapshot,owned.settings.templates)}
    if settings.maintenance_receipt is not None:
        t=TemplateReceipt.model_validate(read(settings.maintenance_receipt));images.add(t.image_id)
        needed.add('release:'+t.runner_release_id+':'+t.template_id)
    rows=[];jobs=[];hashes=set();release_ids=set();keep_all=False;restore_backups=set();updates={};unresolved=[]
    index=load_index(settings);selected=index.channels.get(settings.channel)
    if selected is not None:
        manifest,sha=load_release(settings,selected,expected_sha256=index.releases[selected].sha256,cache=False)
        release_ids.add(selected);hashes.add(sha)
        hashes.update(v.sha256 for v in manifest.wheels.values());hashes.update([manifest.kit.sha256,manifest.test_lock.sha256])
    store=UpdateStore(settings)
    for path in sorted(store.jobs.glob('*.json')):
        if path.stem==ident:continue
        value=store.read(path.stem)
        if value.get('operation')=='gc':continue
        if value.get('operation')=='restore' and value['phase']!='completed':restore_backups.add(value['request']['backup_id'])
        jobs.append({key:value.get(key) for key in ('command_id','operation','request','phase','release_id','task_id','image_id','manifest_sha256','build_input_sha256','prepared_sha256')})
        if value.get('operation'):continue
        if value.get('task_id'):continue
        if value['phase'] not in ('completed','cancelled'):
            release_ids.add(value['release_id'])
            if value.get('manifest_sha256'):
                hashes.add(value['manifest_sha256'])
                try:cached=cached_release(settings,value['release_id'],value['manifest_sha256'])
                except (CloudError,OSError,ValueError) as error:
                    logger.warning('cloud_gc_reference_unresolved command_id=%s error_type=%s',value['command_id'],type(error).__name__);cached=None
                if cached is None:keep_all=True
                else:
                    release=cached[0];hashes.update(v.sha256 for v in release.wheels.values());hashes.update([release.kit.sha256,release.test_lock.sha256])
            else:keep_all=True
        elif value.get('image_id'):updates.setdefault(value['image_id'],[]).append(value)
    backups=list_backups(settings);budget=settings.gc_backup_verify_bytes
    for row in backups:
        if row['phase']!='complete':continue
        manifest,_=_manifest(settings,row['backup_id']);size=sum(v.get('size',0) for v in manifest['entries'])
        if size>budget:row['integrity']='budget_unchecked';continue
        budget-=size
        try:verify_backup(settings,row['backup_id']);row['integrity']='verified'
        except (CloudError,OSError,ValueError) as error:
            logger.warning('cloud_gc_backup_unverified backup_id=%s error_type=%s',row['backup_id'],type(error).__name__);row['integrity']='invalid'
    complete=sorted([v for v in backups if v['phase']=='complete' and v['integrity']=='verified'],key=lambda v:(v['created_at'],v['backup_id']),reverse=True)
    retained={v['backup_id'] for v in complete[:settings.keep_backups]}|restore_backups
    retained.update(v['backup_id'] for v in backups if v.get('integrity')!='verified')
    for row in backups:
        rows.append(row)
        if row['phase']!='complete':keep_all=True;continue
        # A backup proposed for deletion still protects its images in this pass.
        manifest,_=_manifest(settings,row['backup_id'])
        needed.update(manifest['required_releases']);images.update(v['image_id'] for v in manifest['required_templates'])
        hashes.update(_artifact_hashes(manifest['meta'],manifest['required_releases']))
    for upgrade in owned.snapshot['upgrades']:
        if (settings.work_dir/'backups'/upgrade['command_id']).exists():
            if 'reservation' not in upgrade['result'] or 'target_template' not in upgrade['result']:
                # Task deletion redacts old command bodies while the private
                # raw backup remains. Without its dependency proof protect all
                # versions/caches and expose the unresolved reference.
                keep_all=True;unresolved.append(upgrade['command_id'])
                logger.warning('cloud_gc_upgrade_backup_unresolved command_id=%s',upgrade['command_id'])
                continue
            reservation=upgrade['result']['reservation']
            release_ids.update([reservation['source_release_id'],reservation['target_release_id']])
            images.add(upgrade['result']['target_template']['image_id'])
    needed.update(k for k in owned.snapshot['meta'] if k.startswith('release:') and k.split(':')[1] in release_ids)
    hashes.update(_artifact_hashes(owned.snapshot['meta'],needed))
    for job in jobs:
        if job.get('release_id') in {k.split(':')[1] for k in needed} and job.get('manifest_sha256'):hashes.add(job['manifest_sha256'])
    return dict(keys=needed,images=images,hashes=hashes,keep_all=keep_all,updates=updates,backups=backups,unresolved=unresolved,
        retained_backups=retained,external=dict(jobs=jobs,backups=rows,maintenance=sorted(images),channel=selected,
            channel_manifest=index.releases[selected].sha256 if selected else None))


def _artifact_hashes(meta,keys):
    result=set()
    for key in keys:
        value=meta.get(key)
        if value is None:continue
        release=value['release']
        result.update(v['sha256'] for v in release['wheels'].values())
        result.update(release[k]['sha256'] for k in ('kit','test_lock'))
    return result
