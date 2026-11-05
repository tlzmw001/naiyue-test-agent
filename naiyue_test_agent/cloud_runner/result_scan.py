"""Bounded workspace inventory and frozen bytes, using the original input policy."""
import hashlib,os,stat
from pathlib import Path
from naiyue_test_agent.cloud_files.paths import identity,source_handle,check_links
from naiyue_test_agent.contracts.cloud_inputs import InputEntry,check_input_content
from naiyue_test_agent.contracts.cloud_results import OutputEntry,result_path
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory
from .result_git import ignored_paths

SKIP={'.git','.venv','node_modules','__pycache__','.pytest_cache','.cache','.ssh','.aws'}


def excluded(path,output_directory):
    parts=path.split('/')
    return (path==output_directory or path.startswith(output_directory+'/') or any(p.casefold() in SKIP for p in parts)
        or any(p.casefold().startswith('.env') or p.casefold() in {'id_rsa','id_ed25519','id_dsa','id_ecdsa','.netrc','_netrc'}
            or p.casefold().endswith(('.p12','.pfx')) for p in parts))


def inventory(root,limits,*,skip_output='',original=(),ordinary=False):
    root=Path(root);check_store_directory(root);pending=[root];dirs={};files={};seen=0
    while pending:
        parent=pending.pop();check_store_directory(parent)
        members=sorted(os.listdir(parent));dirs[parent.relative_to(root).as_posix()]=(identity(parent.lstat()),members)
        for name in members:
            path=parent/name;relative=path.relative_to(root).as_posix();seen+=1
            if seen>limits.max_discovery_entries:raise CloudError('result_discovery_budget')
            if not ordinary and excluded(relative,skip_output) and not any(p==relative or p.startswith(relative+'/') for p in original):continue
            if ordinary:
                from naiyue_test_agent.contracts.cloud_inputs import file_path
                file_path(relative)
            else:result_path(relative)
            info=path.lstat()
            if ordinary and (stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):raise CloudError('result_artifact_type')
            if stat.S_ISDIR(info.st_mode):pending.append(path)
            else:files[relative]=identity(info)
    return files,dirs


def verify(root,files,dirs):
    for path,expected in files.items():
        if identity((root/path).lstat())!=expected:raise CloudError('result_source_changed')
    for path,(expected,members) in dirs.items():
        target=root/path;check_store_directory(target)
        if identity(target.lstat())!=expected or sorted(os.listdir(target))!=members:raise CloudError('result_source_changed')


def read_content(root,path,expected,limits,*,target=None,ordinary=False):
    """Hash raw bytes once; metadata is only a before/after change detector."""
    digest=hashlib.sha256();size=0;tail=b''
    with source_handle(root,path,expected) as (stream,info):
        length=len(stream) if info is None else info.st_size
        git=hashlib.sha1(b'blob '+str(length).encode()+b'\0')
        if info is None:
            if length>4096:raise CloudError('input_link_outside')
            digest.update(stream);git.update(stream);size=length
            if target is not None:target.write(stream)
            entry=OutputEntry(path=path,type='symlink',size=size,sha256=digest.hexdigest(),mode=0o777,target=stream.decode())
        else:
            if length>limits.max_file_bytes:raise CloudError('result_file_budget')
            while chunk:=stream.read(limits.io_bytes):
                if not ordinary:tail=check_input_content(tail,chunk,first=size==0)
                size+=len(chunk);digest.update(chunk);git.update(chunk)
                if size>limits.max_file_bytes:raise CloudError('result_file_budget')
                if target is not None:target.write(chunk)
            if size!=length:raise CloudError('result_source_changed')
            entry=(InputEntry if ordinary else OutputEntry)(path=path,type='file',size=size,sha256=digest.hexdigest(),mode=0o755 if expected[2]&stat.S_IXUSR else 0o644)
    return entry,git.hexdigest()


def freeze_tree(root,destination,limits,result_limits,*,package=None,output_directory='',known_content=None,ordinary=False):
    root=Path(root);destination.mkdir(mode=0o700)
    original={e.path for e in package.manifest.entries} if package else set()
    if output_directory and any(p==output_directory or p.startswith(output_directory+'/') for p in original):raise CloudError('result_output_conflict')
    files,dirs=inventory(root,limits,skip_output=output_directory,original=original,ordinary=ordinary)
    policy=package.selection.policy if package else None
    ignored=ignored_paths(policy,sorted(files),destination.parent/'policy',result_limits) if policy else set()
    paths=[p for p in sorted(files) if p in original or p not in ignored and not (policy and any(p==v or p.startswith(v+'/') for v in policy.exclude))]
    if len(paths)>result_limits.max_files:raise CloudError('result_file_budget')
    entries=[];blobs={};oids={};total=0
    for number,path in enumerate(paths):
        blob=destination/str(number)
        if known_content is None:
            with blob.open('xb') as target:entry,oid=read_content(root,path,files[path],limits,target=target,ordinary=ordinary)
            blobs[path]=blob
        else:
            entry,oid=read_content(root,path,files[path],limits,ordinary=ordinary)
            known=known_content.get((entry.size,entry.sha256))
            if known is not None and known!=oid:raise CloudError('result_baseline_changed')
            if known is None:
                with blob.open('xb') as target:frozen,frozen_oid=read_content(root,path,files[path],limits,target=target,ordinary=ordinary)
                if frozen!=entry or frozen_oid!=oid:raise CloudError('result_source_changed')
                blobs[path]=blob
        total+=entry.size
        if total>result_limits.max_result_bytes:raise CloudError('result_byte_budget')
        entries.append(entry);oids[path]=oid
    check_links(entries);verify(root,{p:files[p] for p in paths},dirs)
    return entries,blobs,({p:files[p] for p in paths},dirs),oids
