"""Bounded views read only published, sealed result files and trusted input metadata."""
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_results import OutputManifest
from naiyue_test_agent.cloud_files.archive import strict_json
from naiyue_test_agent.contracts.cloud_inputs import input_path as portable_path


def file_slot(service,task_id,result_id,file_id):
    record=service.get(task_id,result_id)
    if record.phase!='available' or record.bundle is None:raise CloudError('result_not_available',404)
    item=next((f for f in record.bundle.files if f.artifact.artifact_id==file_id),None)
    if item is None:raise CloudError('result_file_not_found',404)
    return item


def read_bytes(service,task_id,result_id,file_id,limit,offset=0):
    r=service.runtime
    with r.task_io.hold(task_id):
        slot=file_slot(service,task_id,result_id,file_id)
        with r.gates.hold(task_id):
            r.artifacts._get(task_id,file_id);transfer=r.artifacts.repository.get(task_id,file_id)
        with r.artifacts.store.open(transfer) as stream:
            stream.seek(offset);return slot,stream.read(limit)


def changes(service,task_id,result_id,offset,limit):
    r=service.runtime
    with r.task_io.hold(task_id):
        record=service.get(task_id,result_id)
        if record.phase!='available' or record.bundle is None:raise CloudError('result_not_available',404)
        manifest_slot=next(f.artifact for f in record.bundle.files if f.role=='manifest')
        _,data=read_bytes(service,task_id,result_id,manifest_slot.artifact_id,r.settings.result_limits.max_metadata_bytes+1)
        if len(data)>r.settings.result_limits.max_metadata_bytes:raise CloudError('result_metadata_budget')
        output=OutputManifest.model_validate(strict_json(data));original=r.inputs.get_input(record.plan.input_id)
        before={e.path:e for e in original.manifest.entries};after={e.path:e for e in output.entries};rows=[]
        for path in sorted(before.keys()|after.keys()):
            old,new=before.get(path),after.get(path)
            if old==new:continue
            try:portable_path(path);portable=True
            except (ValueError,CloudError):portable=False
            rows.append(dict(path=path,kind='added' if old is None else 'deleted' if new is None else 'modified',
                before=old.model_dump() if old else None,after=new.model_dump() if new else None,portable=portable))
        return dict(changes=rows[offset:offset+limit],total=len(rows),next_offset=offset+limit if offset+limit<len(rows) else None)


def preview(service,task_id,result_id,file_id,offset,limit):
    slot,data=read_bytes(service,task_id,result_id,file_id,limit+4,offset)
    # Offset is a byte cursor. Don't split the last UTF-8 character.
    binary=b'\0' in data;text='';used=0
    if not binary:
        for trim in range(4):
            candidate=data[:max(0,min(len(data),limit)-trim)]
            try:text=candidate.decode('utf-8');used=len(candidate);break
            except UnicodeDecodeError:continue
        else:binary=True
    return dict(artifact=slot.artifact.model_dump(mode='json'),role=slot.role,binary=binary,text='' if binary else text,
        next_offset=offset+used,complete=offset+used>=slot.artifact.size)
