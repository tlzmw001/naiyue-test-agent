"""Save selected fixed reports to a user-observed folder, without project registration."""
import os
from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_inputs import InputEntry,distinct_paths
from naiyue_test_agent.contracts.cloud_delivery import DeliveryChange
from naiyue_test_agent.cloud_files.delivery_verify import VerifiedOutput,FileListing
from naiyue_test_agent.cloud_files.delivery_apply import apply_changes,export_tree
from naiyue_test_agent.cloud_files.delivery_review import review,approved


def deliver_files(deliveries,domain,record,receive,work,execute):
    _,meta=domain.deliveries.read(record.command_id,record.local_session_id)
    root=deliveries._check_root(domain,meta);limits=deliveries.manager.settings.receive_limits
    reports=[f for f in receive.files if f.role=='artifact']
    if not reports or any(f.role!='artifact' for f in receive.files):raise CloudError('cloud_receive_selection_invalid',422)
    distinct_paths([f.artifact.relative_name for f in reports],ordinary=True)
    required=sum(f.artifact.size for f in reports)*3
    if domain.materials.files.used_bytes(limits)+required>limits.max_cache_bytes:raise CloudError('cloud_delivery_capacity',413)
    with tempfile.TemporaryDirectory(prefix='nyt-reports-') as temporary:
        directory=Path(temporary).resolve();entries=[]
        for file in reports:
            a=file.artifact
            ref,expected=domain.materials.repo.material(file.material_id,record.local_session_id,domain.project_id)
            if ref.receive_id!=receive.receive_id or ref.role!='artifact' or (ref.sha256,ref.size)!=(a.sha256,a.size):
                raise CloudError('cloud_material_identity_mismatch')
            target=directory/a.relative_name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with ExitStack() as stack:
                try:source=stack.enter_context(domain.materials.files.verified(domain.materials.files.blob(a.sha256),a.size,a.sha256,expected))
                except OSError as error:raise CloudError('cloud_material_unavailable') from error
                with target.open('xb') as output:shutil.copyfileobj(source,output,length=262144)
            target.chmod(0o644)
            entries.append(InputEntry(path=a.relative_name,type='file',size=a.size,sha256=a.sha256,mode=0o644))
        entries.sort(key=lambda e:e.path)
        verified=VerifiedOutput(directory,None,FileListing(entries),[dict(path=e.path,old=None,new=e) for e in entries],None,ordinary=True)
        changes=[DeliveryChange(path=e.path,operation='add') for e in entries]
        if not execute:
            if record.name:
                if os.path.lexists(root/record.name):raise CloudError('cloud_delivery_destination_exists')
                conflicts=[]
            else:
                meta['observed'],conflicts=review(root,verified,limits.max_file_bytes)
                changes=[DeliveryChange(path=e.path,operation='modify' if meta['observed'][e.path] else 'add') for e in entries]
                if domain.materials.files.used_bytes(limits)+required+sum(e['size'] for e in meta['observed'].values() if e)>limits.max_cache_bytes:
                    raise CloudError('cloud_delivery_capacity',413)
            meta['verified']=True
            domain.deliveries.save(record.model_copy(update={'phase':'prepared','changes':changes,'conflicts':conflicts,
                'force_allowed':bool(conflicts),'error_code':None}),meta)
            return
        if record.name:
            export_tree(root,record.name,verified,work/'export');done=[e.path for e in entries]
        else:
            verified=approved(root,verified,meta['observed'],meta.get('force',False),continuing=(work/'apply/operation.json').exists())
            def checkpoint(progress):
                meta['done']=list(progress['done'])
                domain.deliveries.save(record.model_copy(update={'done':meta['done']}),meta)
            done=apply_changes(root,verified,work/'apply',checkpoint)['done']
        domain.deliveries.complete(record.model_copy(update={'phase':'completed','done':done,'error_code':None}),meta)
