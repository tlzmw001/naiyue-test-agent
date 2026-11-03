"""Raw input plus a patch must reconstruct exactly the declared complete output."""
from dataclasses import dataclass
import hashlib,logging,os,shutil,tempfile
from pathlib import Path
from pydantic import ValidationError
from naiyue_test_agent.contracts.cloud_inputs import InputManifest,Selection,distinct_paths
from naiyue_test_agent.contracts.cloud_results import OutputManifest
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.application.cloud.ownership import private_directory
from naiyue_test_agent.storage.files import check_store_directory,open_store_readonly
from .archive import validate_package,strict_json
from .paths import identity,source_handle,check_links
from .delivery_git import DeliveryGit,oid
from .delivery_patch_budget import check_patch_budget

logger=logging.getLogger(__name__)


@dataclass(frozen=True)
class VerifiedOutput:
    directory:Path
    input_manifest:InputManifest
    output_manifest:OutputManifest
    changes:list[dict]
    selection:Selection


def _copy_patch(source,target,limit):
    with os.fdopen(open_store_readonly(source),'rb') as stream,target.open('xb') as output:
        before=identity(os.fstat(stream.fileno()));count=0
        while chunk:=stream.read(256*1024):
            count+=len(chunk)
            if count>limit:raise CloudError('cloud_delivery_patch_budget',413)
            output.write(chunk)
        output.flush();os.fsync(output.fileno())
        if identity(os.fstat(stream.fileno()))!=before:raise CloudError('cloud_delivery_source_changed',409)
    return count


def _touched(git,patch):
    parts=git.run(['apply','--numstat','-z','--binary',str(patch)]).split(b'\0');result=set();i=0
    while i<len(parts) and parts[i]:
        columns=parts[i].split(b'\t',2);i+=1
        if len(columns)!=3:raise CloudError('cloud_delivery_patch_invalid',409)
        if columns[2]:result.add(columns[2].decode())
        else:
            if i+1>=len(parts):raise CloudError('cloud_delivery_patch_invalid',409)
            result.update([parts[i].decode(),parts[i+1].decode()]);i+=2
    return result


def _restore(git,tree,manifest,destination,stage,limits):
    listing=git.listing(tree);expected={e.path:e for e in manifest.entries}
    if set(listing)!=set(expected):raise CloudError('cloud_delivery_output_mismatch',409)
    for path,(mode,kind,value) in listing.items():
        entry=expected[path];want='120000' if entry.type=='symlink' else '100755' if entry.mode==0o755 else '100644'
        if kind!='blob' or mode!=want:raise CloudError('cloud_delivery_output_mismatch',409)
    batch=stage/'batch';budget=limits.max_result_bytes+limits.max_files*256
    with batch.open('xb') as output:
        git.run(['cat-file','--batch'],data=b''.join((value[2]+'\n').encode() for value in listing.values()),
            output_stream=output,output_limit=budget)
    destination.mkdir(mode=0o700);links=[]
    with batch.open('rb') as stream:
        for path,(mode,kind,value) in listing.items():
            entry=expected[path];header=stream.readline(256).decode().strip().split()
            if header!=[value,'blob',str(entry.size)]:raise CloudError('cloud_delivery_output_mismatch',409)
            target=destination/path;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            digest=hashlib.sha256();remaining=entry.size;link=bytearray()
            with target.open('xb') as output:
                while remaining:
                    chunk=stream.read(min(remaining,256*1024))
                    if not chunk:raise CloudError('cloud_delivery_output_mismatch',409)
                    remaining-=len(chunk);digest.update(chunk)
                    if entry.type=='symlink':link.extend(chunk)
                    else:output.write(chunk)
                output.flush();os.fsync(output.fileno())
            if digest.hexdigest()!=entry.sha256 or stream.read(1)!=b'\n':raise CloudError('cloud_delivery_output_mismatch',409)
            if entry.type=='symlink':
                if link.decode()!=entry.target:raise CloudError('cloud_delivery_output_mismatch',409)
                target.unlink();links.append((target,entry.target))
            else:target.chmod(entry.mode)
        if stream.read(1):raise CloudError('cloud_delivery_output_mismatch',409)
    for path,target in links:path.symlink_to(target)


def verify_delivery(package,archive,manifest_raw,patch_path,work,input_limits,result_limits):
    work=Path(work).absolute();work.mkdir(mode=0o700,parents=True,exist_ok=True);check_store_directory(work);private_directory(work)
    stage=Path(tempfile.mkdtemp(prefix='verify-',dir=work))
    try:
        if len(manifest_raw)>result_limits.max_metadata_bytes:raise CloudError('cloud_delivery_metadata_budget',413)
        output=OutputManifest.model_validate(strict_json(manifest_raw))
        if (output.canonical!=manifest_raw or output.input_id!=package.input_id
                or output.policy_sha256!=package.manifest.selection['policy_sha256']):raise CloudError('cloud_delivery_identity_mismatch',409)
        distinct_paths([e.path for e in output.entries]);check_links(output.entries)
        if (len(output.entries)>result_limits.max_files or sum(e.size for e in output.entries)>result_limits.max_result_bytes
                or any(e.size>input_limits.max_file_bytes or e.type=='symlink' and e.size>4096 for e in output.entries)):
            raise CloudError('cloud_delivery_output_budget',413)
        baseline=stage/'input';baseline.mkdir(mode=0o700)
        original,selection=validate_package(archive,package.codec,input_limits,expected_id=package.input_id,destination=baseline,
            expected_archive_sha256=package.archive_sha256,expected_size=package.size)
        if original!=package.manifest or selection!=package.selection:raise CloudError('cloud_delivery_identity_mismatch',409)
        blobs=stage/'blobs';blobs.mkdir(mode=0o700);paths=[]
        for number,entry in enumerate(original.entries):
            path=blobs/str(number)
            with source_handle(baseline,entry.path,identity((baseline/entry.path).lstat())) as (stream,info),path.open('xb') as target:
                if info is None:target.write(stream)
                else:shutil.copyfileobj(stream,target,length=input_limits.io_bytes)
            paths.append(path)
        git=DeliveryGit(stage/'git',result_limits);hashes=git.hashes(paths)
        git.run(['read-tree','--empty'])
        git.update([(e.path,'120000' if e.type=='symlink' else '100755' if e.mode==0o755 else '100644',value) for e,value in zip(original.entries,hashes)])
        patch=stage/'patch';size=_copy_patch(patch_path,patch,result_limits.max_patch_bytes)
        old={e.path:e for e in original.entries};new={e.path:e for e in output.entries}
        changes=[dict(path=p,old=old.get(p),new=new.get(p)) for p in sorted(old.keys()|new.keys())
            if old.get(p) is None or new.get(p) is None or old[p].model_dump()!=new[p].model_dump()]
        if size:
            check_patch_budget(patch,input_limits,result_limits)
            if _touched(git,patch)!={v['path'] for v in changes}:raise CloudError('cloud_delivery_patch_paths_mismatch',409)
            git.run(['apply','--cached','--binary','--whitespace=nowarn',str(patch)])
        elif changes:raise CloudError('cloud_delivery_patch_paths_mismatch',409)
        tree=oid(git.run(['write-tree']));destination=stage/'output'
        _restore(git,tree,output,destination,stage,result_limits)
        return VerifiedOutput(destination,original,output,changes,selection)
    except (OSError,ValueError,UnicodeError,ValidationError) as error:
        logger.warning('cloud_delivery_verification_failed error_type=%s',type(error).__name__)
        shutil.rmtree(stage)
        raise CloudError('cloud_delivery_verification_failed',409) from error
    except BaseException:
        shutil.rmtree(stage);raise
