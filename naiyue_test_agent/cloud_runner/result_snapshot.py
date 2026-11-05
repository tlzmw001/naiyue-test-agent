"""Publish one stable O/patch/report spool; a published result is never rescanned."""
import hashlib,json,os,shutil,tempfile,time
from pathlib import Path
from uuid import uuid4
from naiyue_test_agent.cloud_files.paths import identity
from naiyue_test_agent.contracts.cloud_inputs import InputPackage
from naiyue_test_agent.contracts.cloud_results import OutputManifest,ResultBundle,ResultFile,ResultRecord
from naiyue_test_agent.contracts.cloud_artifacts import ArtifactRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import open_store_readonly,sync_store_directory,check_store_directory
from .result_scan import freeze_tree,verify
from .result_git import ResultGit
from .result_baseline import prepare
from .state import read_json,write_json


def verify_spool(path,plan,boundary,limits):
    check_store_directory(path)
    state=ResultRecord.model_validate(read_json(path/'result.json',limits.max_metadata_bytes))
    if state.plan!=plan or state.boundary!=boundary or state.bundle is None:raise CloudError('result_spool_identity')
    for file in state.bundle.files:
        a=file.artifact;sha=hashlib.sha256();size=0
        with os.fdopen(open_store_readonly(path/'files'/a.artifact_id),'rb') as stream:
            before=identity(os.fstat(stream.fileno()))
            if os.fstat(stream.fileno()).st_size!=a.size or a.size>limits.max_result_bytes:raise CloudError('result_spool_changed')
            while chunk:=stream.read(min(256*1024,a.size-size+1)):
                size+=len(chunk);sha.update(chunk)
                if size>a.size:raise CloudError('result_spool_changed')
            if identity(os.fstat(stream.fileno()))!=before:raise CloudError('result_spool_changed')
        if (size,sha.hexdigest())!=(a.size,a.sha256):raise CloudError('result_spool_changed')
    return state


def freeze_result(root,input_directory,destination,plan,boundary,generation,result_limits,input_limits):
    root,input_directory,destination=map(lambda p:Path(p).absolute(),(root,input_directory,destination))
    if destination.is_relative_to(root) or root.is_relative_to(destination):raise CloudError('result_capture_overlap')
    if os.path.lexists(destination):verify_spool(destination,plan,boundary,result_limits);return destination
    destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True);check_store_directory(destination.parent)
    try:
        if plan.input_id is None:
            from .output_snapshot import freeze_outputs
            return freeze_outputs(root,destination,plan,boundary,generation,result_limits,input_limits)
        with os.fdopen(open_store_readonly(input_directory/'input.json'),'rb') as f:
            package=InputPackage.model_validate_json(f.read(input_limits.max_metadata_bytes*3+1))
        if package.input_id!=plan.input_id:raise CloudError('result_input_mismatch')
        baseline=prepare(destination.parent,input_directory,package,result_limits,input_limits)
        with tempfile.TemporaryDirectory(prefix='.result-',dir=destination.parent) as temporary:
            stage=Path(temporary)
            entries,blobs,proof,oids=freeze_tree(root,stage/'output',input_limits,result_limits,package=package,
                output_directory=plan.output_directory,known_content=baseline.known_content)
            output=OutputManifest(input_id=plan.input_id,policy_sha256=package.manifest.selection['policy_sha256'],entries=entries)
            raw_manifest=output.canonical
            if len(raw_manifest)>result_limits.max_metadata_bytes:raise CloudError('result_metadata_budget')
            git=ResultGit(stage/'compare',result_limits,alternate=baseline.directory/'objects.git/objects')
            for path,oid in git.hashes(blobs).items():
                if oids[path]!=oid:raise CloudError('result_source_changed')
            new=git.tree_oids(entries,oids,known_objects=baseline.object_ids)
            patch=stage/'changes.patch';git.patch(baseline.tree,new,patch,result_limits.max_patch_bytes)
            result=stage/'published';result.mkdir(mode=0o700);files_dir=result/'files';files_dir.mkdir(mode=0o700)
            captured=time.time();files=[];total=0
            def add(role,name,source=None,content=None):
                nonlocal total
                artifact_id=str(uuid4());path=files_dir/artifact_id
                if content is not None:path.write_bytes(content)
                else:shutil.copyfile(source,path)
                path.chmod(0o600);sha=hashlib.sha256();size=0
                with path.open('r+b') as stream:
                    while chunk:=stream.read(input_limits.io_bytes):size+=len(chunk);sha.update(chunk)
                    os.fsync(stream.fileno())
                total+=size
                if size>result_limits.max_patch_bytes and role=='patch' or total>result_limits.max_result_bytes:raise CloudError('result_byte_budget')
                files.append(ResultFile(role=role,artifact=ArtifactRecord(command_id=plan.result_id,artifact_id=artifact_id,
                    relative_name=name,task_id=plan.task_id,project_id=plan.project_id,input_id=plan.input_id,generation=generation,
                    captured_at=captured,size=size,sha256=sha.hexdigest())))
            add('manifest','output-manifest.json',content=raw_manifest);add('patch','changes.patch',source=patch)
            reports=root/plan.output_directory/plan.source_run_id
            report_proof=None
            if os.path.lexists(reports):
                report_entries,report_blobs,report_proof,_=freeze_tree(reports,stage/'reports',input_limits,result_limits)
                if len(report_entries)>result_limits.max_artifacts:raise CloudError('result_artifact_budget')
                for entry in report_entries:
                    if entry.type!='file':raise CloudError('result_artifact_type')
                    add('artifact',entry.path,source=report_blobs[entry.path])
            verify(root,*proof)
            if report_proof is not None:verify(reports,*report_proof)
            bundle=ResultBundle(plan=plan,boundary=boundary,generation=generation,captured_at=captured,files=files)
            state=ResultRecord(plan=plan,phase='verifying',boundary=boundary,bundle=bundle)
            if len(state.model_dump_json().encode())>result_limits.max_metadata_bytes:raise CloudError('result_metadata_budget')
            write_json(result/'result.json',state.model_dump(mode='json'));sync_store_directory(files_dir);sync_store_directory(result)
            os.rename(result,destination);sync_store_directory(destination.parent)
        return destination
    except CloudError:raise
    except (OSError,ValueError,UnicodeError) as error:raise CloudError('result_capture_failed') from error
