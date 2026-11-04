"""Fixed isolated raw restore; untrusted task SQLite is never parsed by the host."""
import argparse,json,os,re
from pathlib import Path
from pydantic import TypeAdapter

from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from naiyue_test_agent.contracts.cloud_releases import UpgradeBackup
from naiyue_test_agent.contracts.cloud_results import ResultPlan
from naiyue_test_agent.contracts.cloud_events import digest
from naiyue_test_agent.storage.raw_tree import copy_roots,verify_tree,restore_modes,remove_tree
from .upgrade_backup import _verified_existing
from .state import read_json


def restore(source,workspace,state,value,*,max_bytes,max_files):
    receipt=UpgradeBackup.model_validate(value['backup'])
    request={key:getattr(receipt,key) for key in ('command_id','task_id','generation','reservation_sha256')}
    if _verified_existing(source,request,max_bytes,max_files)!=receipt.model_dump():raise CloudError('backup_invalid')
    complete=read_json(source/'complete.json');payload=source/complete['payload']
    manifest=read_json(payload/'manifest.json',max_files*8192+1024*1024)
    for target in (workspace,state):
        if any(target.iterdir()):raise CloudError('restore_target_not_empty')
    records=copy_roots([('workspace',payload/'workspace',workspace),('state',payload/'state',state)],max_bytes=max_bytes,max_files=max_files)
    expected={e['path']:{k:v for k,v in e.items() if k!='mode'} for e in manifest['entries']}
    if {e['path']:{k:v for k,v in e.items() if k!='mode'} for e in records}!=expected:raise CloudError('backup_invalid')
    restore_modes({'workspace':workspace,'state':state},manifest['entries'])
    retired=value.get('retired_results',{})
    for ident,sha in retired.items():
        TypeAdapter(UUIDText).validate_python(ident)
        if not re.fullmatch('[a-f0-9]{64}',sha):raise CloudError('restore_result_unconfirmed')
    plans=state/'result-plans'
    if plans.exists():
        for path in sorted(plans.iterdir()):
            if path.name.endswith('.json.next'):continue
            try:
                local=read_json(path);plan=ResultPlan.model_validate(local['plan'])
            except (CloudError,ValueError,KeyError):raise CloudError('restore_result_unconfirmed') from None
            if (path.name!=plan.result_id+'.json' or plan.task_id!=receipt.task_id or plan.source_generation>receipt.generation
                    or retired.get(plan.result_id)!=digest(plan.model_dump(mode='json'))):raise CloudError('restore_result_unconfirmed')
            remove_tree(path,max_files=max_files)
    spools=state/'results'
    if spools.exists():
        for path in sorted(spools.iterdir()):
            if path.name!='.baseline-cache' and path.name not in retired:raise CloudError('restore_result_unconfirmed')
            remove_tree(path,max_files=max_files)
    return dict(task_id=receipt.task_id,files=receipt.files,bytes=receipt.bytes,regenerable_results_reset=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--max-bytes',type=int,required=True);p.add_argument('--max-files',type=int,required=True)
    args=p.parse_args()
    try:
        result=restore(Path('/backup'),Path('/workspace'),Path('/state'),read_json(Path('/request.json'),8*1024*1024),max_bytes=args.max_bytes,max_files=args.max_files)
        print(json.dumps(result));return 0
    except (CloudError,OSError,ValueError) as error:
        print(json.dumps(dict(status='failed',code=error.code if isinstance(error,CloudError) else 'restore_task_failed')));return 1


if __name__=='__main__':raise SystemExit(main())
