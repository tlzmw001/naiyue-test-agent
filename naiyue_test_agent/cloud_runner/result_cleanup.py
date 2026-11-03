"""Retire one confirmed result using fixed names and pinned no-follow directory FDs."""
import os
import stat

from pydantic import TypeAdapter
from naiyue_test_agent.application.cloud.ownership import private_directory
from naiyue_test_agent.application.cloud.resource_disk import open_directory,mount_id
from naiyue_test_agent.contracts.cloud_results import ResultRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from .state import read_json


def directory_identity(info):
    return [info.st_dev,info.st_ino,info.st_mode]


def file_identity(info):
    return directory_identity(info)+[info.st_size,info.st_mtime_ns,info.st_ctime_ns]


def _safe(info,kind):
    if (getattr(info,'st_file_attributes',0)&0x400 or
            not (stat.S_ISDIR(info.st_mode) if kind=='directory' else stat.S_ISREG(info.st_mode)) or
            kind=='file' and info.st_nlink!=1):
        raise CloudError('result_cleanup_type')


def _same(fd,name,expected,kind):
    try:info=os.stat(name,dir_fd=fd,follow_symlinks=False)
    except FileNotFoundError:return None
    _safe(info,kind)
    if (directory_identity(info) if kind=='directory' else file_identity(info))!=expected:
        raise CloudError('result_cleanup_changed')
    return info


def _open_child(parent,name,expected,mount):
    if _same(parent,name,expected,'directory') is None:return None
    child=open_directory(name,dir_fd=parent)
    try:
        if directory_identity(os.fstat(child))!=expected or mount_id(child)!=mount:
            raise CloudError('result_cleanup_changed')
        return child
    except BaseException:
        os.close(child);raise


def _names(fd,allowed):
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.name not in allowed:raise CloudError('result_cleanup_shape')


def check_roots(directory,spools,expected):
    for path,key in ((directory,'plans'),(spools,'spools')):
        private_directory(path)
        if directory_identity(path.lstat())!=expected[key]:raise CloudError('result_cleanup_changed')


def cleanup_proof(directory,spools,plan,limits,expected):
    """Persist this proof in the private plan before deleting any spool entry."""
    check_roots(directory,spools,expected);base=open_directory(spools)
    try:
        if directory_identity(os.fstat(base))!=expected['spools']:raise CloudError('result_cleanup_changed')
        try:info=os.stat(plan.result_id,dir_fd=base,follow_symlinks=False)
        except FileNotFoundError:return None
        _safe(info,'directory');root_id=directory_identity(info)
        root=_open_child(base,plan.result_id,root_id,mount_id(base))
        try:
            _names(root,{'result.json','files'})
            info=os.stat('result.json',dir_fd=root,follow_symlinks=False);_safe(info,'file');metadata=file_identity(info)
            record=ResultRecord.model_validate(read_json(spools/plan.result_id/'result.json',limits.max_metadata_bytes))
            if record.plan!=plan or record.bundle is None:raise CloudError('result_cleanup_identity')
            if _same(root,'result.json',metadata,'file') is None:raise CloudError('result_cleanup_changed')
            info=os.stat('files',dir_fd=root,follow_symlinks=False);_safe(info,'directory');files_id=directory_identity(info)
            files=_open_child(root,'files',files_id,mount_id(root))
            try:
                names=[item.artifact.artifact_id for item in record.bundle.files]
                _names(files,set(names));proof={}
                for name in names:
                    TypeAdapter(UUIDText).validate_python(name)
                    info=os.stat(name,dir_fd=files,follow_symlinks=False);_safe(info,'file')
                    proof[name]=file_identity(info)
                return dict(root=root_id,files=files_id,metadata=metadata,artifacts=proof)
            finally:os.close(files)
        finally:os.close(root)
    finally:os.close(base)


def remove_confirmed(directory,spools,plan,proof,expected):
    """Missing entries are retryable; changed objects and foreign names are never removed."""
    check_roots(directory,spools,expected);base=open_directory(spools)
    try:
        if directory_identity(os.fstat(base))!=expected['spools']:raise CloudError('result_cleanup_changed')
        if proof is None:
            if os.path.lexists(spools/plan.result_id):raise CloudError('result_cleanup_changed')
        else:
            for name in proof['artifacts']:TypeAdapter(UUIDText).validate_python(name)
            root=_open_child(base,plan.result_id,proof['root'],mount_id(base))
            if root is not None:
                try:
                    _names(root,{'result.json','files'})
                    _same(root,'result.json',proof['metadata'],'file')
                    files=_open_child(root,'files',proof['files'],mount_id(root))
                    if files is not None:
                        try:
                            _names(files,set(proof['artifacts']))
                            # Validate every remaining entry before starting destructive work.
                            for name,identity in proof['artifacts'].items():_same(files,name,identity,'file')
                            for name,identity in proof['artifacts'].items():
                                if (_same(base,plan.result_id,proof['root'],'directory') is None or
                                        _same(root,'files',proof['files'],'directory') is None):
                                    raise CloudError('result_cleanup_changed')
                                if _same(files,name,identity,'file') is not None:os.unlink(name,dir_fd=files)
                            os.fsync(files)
                        finally:os.close(files)
                        if _same(root,'files',proof['files'],'directory') is None:raise CloudError('result_cleanup_changed')
                        os.rmdir('files',dir_fd=root);os.fsync(root)
                    if _same(base,plan.result_id,proof['root'],'directory') is None:raise CloudError('result_cleanup_changed')
                    if _same(root,'result.json',proof['metadata'],'file') is not None:os.unlink('result.json',dir_fd=root)
                    os.fsync(root)
                finally:os.close(root)
                if _same(base,plan.result_id,proof['root'],'directory') is None:raise CloudError('result_cleanup_changed')
                os.rmdir(plan.result_id,dir_fd=base)
        os.fsync(base)
    finally:os.close(base)
    # The plan is the retry anchor and must always be removed last.
    check_roots(directory,spools,expected);plans=open_directory(directory)
    try:
        if directory_identity(os.fstat(plans))!=expected['plans']:raise CloudError('result_cleanup_changed')
        name=plan.result_id+'.json';before=os.stat(name,dir_fd=plans,follow_symlinks=False);_safe(before,'file')
        local=read_json(directory/name)
        if not local.get('done') or local.get('plan')!=plan.model_dump(mode='json'):raise CloudError('result_cleanup_identity')
        if _same(plans,name,file_identity(before),'file') is None:raise CloudError('result_cleanup_changed')
        os.unlink(name,dir_fd=plans);os.fsync(plans)
    finally:os.close(plans)
