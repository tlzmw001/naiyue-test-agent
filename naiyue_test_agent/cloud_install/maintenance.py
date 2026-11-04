"""Explicit verified executor selection is independent of a task's business image."""
import json,os
from naiyue_test_agent.contracts.cloud_tasks import CloudError,TemplateReceipt
from .store import read
from .upgrade import _run


def select_maintenance_template(settings,owned):
    if settings.maintenance_receipt is not None:value=read(settings.maintenance_receipt)
    else:
        active=owned.snapshot['meta'].get('release-active:'+settings.build.template_id)
        record=owned.snapshot['meta'].get('release:'+active['release_id']+':'+settings.build.template_id) if active else None
        value=record['template'] if record else owned.settings.templates.get(settings.build.template_id)
    if value is None:raise CloudError('maintenance_template_required')
    t=TemplateReceipt.model_validate(value)
    if ((t.backend_id,t.uid,t.gid)!=(settings.build.backend_id,settings.build.uid,settings.build.gid)
            or (t.uid,t.gid)!=(os.getuid(),os.getgid())):raise CloudError('maintenance_template_identity_mismatch')
    inspect=json.loads(_run(settings,['image','inspect',t.image_id]))[0]
    if inspect.get('Id')!=t.image_id:raise CloudError('maintenance_template_identity_mismatch')
    try:
        value=json.loads(_run(settings,['run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--memory',settings.backup_memory,'--cpus',str(settings.backup_cpus),'--pids-limit',str(settings.backup_pids),
            '--user',f'{t.uid}:{t.gid}',t.image_id,'/opt/nyt/runner/bin/python','-I','-m','naiyue_test_agent.cloud_runner.maintenance_probe']))
    except (CloudError,ValueError):raise CloudError('maintenance_template_unavailable') from None
    if value!=dict(maintenance_protocol=1,raw_backup=True,raw_restore=True,uid=t.uid,gid=t.gid):raise CloudError('maintenance_template_unavailable')
    return t
