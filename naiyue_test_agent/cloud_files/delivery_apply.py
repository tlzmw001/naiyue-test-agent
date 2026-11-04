"""Raw output installation; each path is journalled before user filesystem effects."""
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import stat
from uuid import uuid4
from naiyue_test_agent.cloud_runner.state import read_json,write_json
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import sync_store_directory,open_store_file
from .delivery_paths import parent,lstat,replace_entry,directory_identity,rename_new,component,portable_conflicts
from .paths import source_handle,identity

logger=logging.getLogger(__name__)


def matches(root,path,entry):
    try:
        with parent(root,path) as (fd,directory,name):
            try:info=lstat(fd,directory,name)
            except FileNotFoundError:return entry is None
            if entry is None:return False
            if entry.type=='symlink':
                target=os.readlink(name,dir_fd=fd) if fd is not None else os.readlink(directory/name)
                return stat.S_ISLNK(info.st_mode) and target==entry.target
            if not stat.S_ISREG(info.st_mode) or info.st_size!=entry.size:return False
            if os.name!='nt' and (0o755 if info.st_mode&0o111 else 0o644)!=entry.mode:return False
            sha=hashlib.sha256()
            with source_handle(Path(root),path,identity(info)) as (stream,_):
                while chunk:=stream.read(262144):sha.update(chunk)
            return sha.hexdigest()==entry.sha256
    except FileNotFoundError:return entry is None
    except (OSError,CloudError) as error:
        logger.warning('cloud_delivery_path_check_failed error_type=%s',type(error).__name__)
        return False


def preflight(root,verified):
    invalid=portable_conflicts(c['path'] for c in verified.changes)
    if invalid:return invalid
    conflicts=[]
    for change in verified.changes:
        try:
            # An absent file below an unsafe parent is not a safe add.
            with parent(root,change['path']):pass
        except FileNotFoundError:pass
        except (OSError,CloudError):conflicts.append(change['path']);continue
        if not matches(root,change['path'],change['old']):conflicts.append(change['path'])
    return conflicts


def apply_changes(root,verified,journal,checkpoint):
    root=Path(root);journal=Path(journal);journal.mkdir(mode=0o700,exist_ok=True)
    path=journal/'operation.json';signature=directory_identity(root)
    expected=[dict(path=c['path'],old=c['old'].model_dump() if c['old'] else None,
        new=c['new'].model_dump() if c['new'] else None) for c in verified.changes]
    if path.exists():
        budget=len(json.dumps(expected,ensure_ascii=False).encode())+sum(len(c['path'].encode())+8 for c in expected)+65536
        state=read_json(path,budget)
        if state['root_identity']!=signature or state['changes']!=expected:raise CloudError('cloud_delivery_identity_changed')
    else:
        conflicts=preflight(root,verified)
        if conflicts:raise CloudError('cloud_delivery_conflict')
        # All backups and intent are durable before the first project mutation.
        for index,c in enumerate(verified.changes):
            if c['old'] is not None:
                info=(root/c['path']).lstat()
                with source_handle(root,c['path'],identity(info)) as (stream,_):
                    with os.fdopen(open_store_file(journal/str(index)),'wb') as out:
                        out.truncate(0)
                        if isinstance(stream,bytes):out.write(stream)
                        else:
                            while chunk:=stream.read(262144):out.write(chunk)
                        out.flush();os.fsync(out.fileno())
        state=dict(root_identity=signature,changes=expected,done=[])
        write_json(path,state);sync_store_directory(journal);checkpoint(state)
    # Validate the entire continuation before adding further side effects.
    for c in verified.changes:
        if c['path'] in state['done']:
            if not matches(root,c['path'],c['new']):raise CloudError('cloud_delivery_conflict')
        elif not matches(root,c['path'],c['old']) and not matches(root,c['path'],c['new']):raise CloudError('cloud_delivery_conflict')
    ordered=sorted(verified.changes,key=lambda c:(c['new'] is not None,-c['path'].count('/') if c['new'] is None else c['path'].count('/'),c['path']))
    for c in ordered:
        name=c['path']
        if name in state['done']:continue
        if not matches(root,name,c['new']):
            with parent(root,name,create=c['new'] is not None) as (fd,directory,base):
                if not matches(root,name,c['old']):raise CloudError('cloud_delivery_conflict')
                if c['new'] is None:
                    if fd is not None:os.unlink(base,dir_fd=fd);os.fsync(fd)
                    else:os.unlink(directory/base)
                else:replace_entry(fd,directory,base,verified.directory/name,c['new'],
                    validate=lambda:matches(root,name,c['old']),create=c['old'] is None)
        if not matches(root,name,c['new']):raise CloudError('cloud_delivery_postcheck_failed')
        state['done'].append(name);write_json(path,state);checkpoint(state)
    return dict(done=state['done'])


def export_tree(root,name,verified,journal):
    if portable_conflicts(e.path for e in verified.output_manifest.entries):raise CloudError('cloud_delivery_path_unsupported',422)
    component(name);root=Path(root);journal=Path(journal);journal.mkdir(mode=0o700,exist_ok=True)
    marker=journal/'export.json'
    state=None
    if marker.exists():
        state=read_json(marker)
        if state['root_identity']!=directory_identity(root) or state['name']!=name:raise CloudError('cloud_delivery_identity_changed')
        if state['phase']=='completed':return dict(name=name)
        if os.path.lexists(root/name):
            if (state['phase']=='publishing' and state.get('staging_identity')==directory_identity(root/name)
                    and _matches_output(root/name,verified)):
                state['phase']='completed';write_json(marker,state);return dict(name=name)
            raise CloudError('cloud_delivery_export_unknown')
    if os.path.lexists(root/name):raise CloudError('cloud_delivery_destination_exists')
    if state is None:
        staging=root/('.nyt-export-'+uuid4().hex)
        state=dict(name=name,root_identity=directory_identity(root),phase='installing',staging=staging.name)
        write_json(marker,state)
    else:
        component(state['staging'])
        if not state['staging'].startswith('.nyt-export-'):raise CloudError('cloud_delivery_identity_changed')
        staging=root/state['staging']
    with parent(root,name) as (fd,_,__):
        if os.path.lexists(staging):
            if state.get('staging_identity')!=directory_identity(staging):raise CloudError('cloud_delivery_identity_changed')
            if not _matches_output(staging,verified):_resume_export(staging,verified)
        else:
            staging.mkdir(mode=0o700)
            state['staging_identity']=directory_identity(staging);write_json(marker,state)
            shutil.copytree(verified.directory,staging,symlinks=True,dirs_exist_ok=True)
        if not _matches_output(staging,verified):raise CloudError('cloud_delivery_postcheck_failed')
        # Reject special objects before opening; pin ordinary files against swaps.
        for directory,_,files in os.walk(staging,followlinks=False):
            for filename in files:
                file=Path(directory)/filename;info=file.lstat()
                if stat.S_ISLNK(info.st_mode):continue
                if not stat.S_ISREG(info.st_mode):raise CloudError('cloud_delivery_postcheck_failed')
                with source_handle(staging,file.relative_to(staging).as_posix(),identity(info)) as (pinned,_),file.open('r+b') as stream:
                    if identity(os.fstat(stream.fileno()))!=identity(os.fstat(pinned.fileno())):
                        raise CloudError('cloud_delivery_identity_changed')
                    os.fsync(stream.fileno())
            sync_store_directory(Path(directory))
        if not _matches_output(staging,verified):raise CloudError('cloud_delivery_postcheck_failed')
        state.update(phase='publishing',staging_identity=directory_identity(staging));write_json(marker,state)
        try:rename_new(staging.name if fd is not None else staging,name if fd is not None else root/name,dir_fd=fd)
        except FileExistsError:raise CloudError('cloud_delivery_destination_exists') from None
        sync_store_directory(root)
        state['phase']='completed';write_json(marker,state)
    return dict(name=name)


def _matches_output(directory,verified):
    expected={e.path:e for e in verified.output_manifest.entries}
    actual=set()
    for root,dirs,files in os.walk(directory,followlinks=False):
        for name in [*dirs,*files]:
            path=Path(root)/name;mode=path.lstat().st_mode;relative=path.relative_to(directory).as_posix()
            if stat.S_ISLNK(mode) or stat.S_ISREG(mode):actual.add(relative)
            elif stat.S_ISDIR(mode):
                if not any(p.startswith(relative+'/') for p in expected):return False
            else:return False
    return actual==set(expected) and all(matches(directory,path,e) for path,e in expected.items())


def _resume_export(directory,verified):
    expected={e.path:e for e in verified.output_manifest.entries}
    for root,dirs,files in os.walk(directory,followlinks=False):
        for name in [*dirs,*files]:
            path=Path(root)/name;relative=path.relative_to(directory).as_posix()
            if path.is_symlink() or not path.is_dir():
                if relative not in expected:raise CloudError('cloud_delivery_export_conflict')
            elif not any(p.startswith(relative+'/') for p in expected):raise CloudError('cloud_delivery_export_conflict')
    for path,entry in expected.items():
        if matches(directory,path,entry):continue
        with parent(directory,path,create=True) as (fd,where,name):
            try:info=lstat(fd,where,name)
            except FileNotFoundError:
                replace_entry(fd,where,name,verified.directory/path,entry,validate=lambda:matches(directory,path,None),create=True)
                continue
            if (entry.type!='file' or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>entry.size
                    or getattr(info,'st_file_attributes',0)&0x400):raise CloudError('cloud_delivery_export_conflict')
            flags=os.O_RDWR|os.O_APPEND|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0)
            descriptor=os.open(name if fd is not None else where/name,flags,**({'dir_fd':fd} if fd is not None else {}))
            with os.fdopen(descriptor,'r+b') as current,(verified.directory/path).open('rb') as source:
                before=os.fstat(current.fileno())
                # Windows path ctime can be birth time while the handle reports
                # change time. Compare each clock only with its own later sample.
                if ((before.st_dev,before.st_ino,before.st_size)!=(info.st_dev,info.st_ino,info.st_size)
                        or identity(lstat(fd,where,name))!=identity(info)):raise CloudError('cloud_delivery_export_conflict')
                remaining=before.st_size
                while remaining:
                    count=min(remaining,262144);old=current.read(count);wanted=source.read(count)
                    if old!=wanted or not old:raise CloudError('cloud_delivery_export_conflict')
                    remaining-=len(old)
                # Only extend an exact prefix. Existing user bytes are never overwritten.
                if (identity(os.fstat(current.fileno()))!=identity(before)
                        or identity(lstat(fd,where,name))!=identity(info)):raise CloudError('cloud_delivery_export_conflict')
                while chunk:=source.read(262144):current.write(chunk)
                current.flush();os.fsync(current.fileno())
                if fd is not None:os.fchmod(current.fileno(),entry.mode)
                else:os.chmod(where/name,entry.mode)
        if not matches(directory,path,entry):raise CloudError('cloud_delivery_export_conflict')
