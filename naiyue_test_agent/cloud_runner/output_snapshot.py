"""Freeze only a run's output directory; no input package or Git objects."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4
from naiyue_test_agent.contracts.cloud_results import ResultBundle,ResultFile,ResultRecord
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import sync_store_directory,check_store_directory
from .result_scan import freeze_tree,verify
from .state import write_json


def freeze_outputs(root,destination,plan,boundary,generation,result_limits,input_limits):
    with tempfile.TemporaryDirectory(prefix='.output-',dir=destination.parent) as temporary:
        stage=Path(temporary);result=stage/'published';result.mkdir(mode=0o700)
        files_dir=result/'files';files_dir.mkdir(mode=0o700)
        captured=time.time();files=[];reports=root/plan.output_directory/plan.source_run_id
        if os.path.lexists(reports):
            check_store_directory(reports)
            entries,blobs,proof,_=freeze_tree(reports,stage/'reports',input_limits,result_limits,ordinary=True)
            if len(entries)>result_limits.max_artifacts:raise CloudError('result_artifact_budget')
            for entry in entries:
                if entry.type!='file':raise CloudError('result_artifact_type')
                ident=str(uuid4());path=files_dir/ident;os.rename(blobs[entry.path],path);path.chmod(0o600)
                # Recheck frozen bytes and durability using a writable FD (Windows).
                sha=hashlib.sha256();size=0
                with path.open('r+b') as stream:
                    while chunk:=stream.read(input_limits.io_bytes):sha.update(chunk);size+=len(chunk)
                    os.fsync(stream.fileno())
                if size!=entry.size or sha.hexdigest()!=entry.sha256:raise CloudError('result_source_changed')
                files.append(ResultFile(role='artifact',artifact=ArtifactRecord(command_id=plan.result_id,
                    artifact_id=ident,relative_name=entry.path,task_id=plan.task_id,project_id=plan.project_id,
                    input_id=None,generation=generation,captured_at=captured,size=size,sha256=entry.sha256)))
            verify(reports,*proof)
        bundle=ResultBundle(plan=plan,boundary=boundary,generation=generation,captured_at=captured,files=files)
        record=ResultRecord(plan=plan,phase='verifying',boundary=boundary,bundle=bundle)
        if len(record.model_dump_json().encode())>result_limits.max_metadata_bytes:raise CloudError('result_metadata_budget')
        write_json(result/'result.json',record.model_dump(mode='json'))
        sync_store_directory(files_dir);sync_store_directory(result)
        os.rename(result,destination);sync_store_directory(destination.parent)
        return destination
