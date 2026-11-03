"""Pinned local delivery parents and no-clobber directory publication."""
from contextlib import contextmanager,ExitStack
import ctypes
import os
from pathlib import Path,PurePosixPath
import stat
import sys
from uuid import uuid4
from naiyue_test_agent.contracts.cloud_inputs import input_path
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import check_store_directory


def component(name):
    try:
        if input_path(name)!=name or '/' in name:raise ValueError('not_component')
    except ValueError:raise CloudError('cloud_delivery_name_invalid',422) from None
    return name


def directory_identity(path):
    from naiyue_test_agent.application.projects.locations import directory_facts
    check_store_directory(path)
    return directory_facts(path)


@contextmanager
def parent(root,path,*,create=False):
    """Keep every ancestor pinned; Windows handles deny directory replacement."""
    input_path(path);root=Path(root);before=directory_identity(root)
    with ExitStack() as stack:
        if os.name=='nt':
            from naiyue_test_agent.application.projects.windows_directory import directory_handle,signature,mkdir_relative
            current=root;handle=stack.enter_context(directory_handle(current));signature(handle)
            for part in PurePosixPath(path).parts[:-1]:
                target=current/part
                if create and not os.path.lexists(target):mkdir_relative(handle,part)
                current=target;handle=stack.enter_context(directory_handle(current));signature(handle)
            yield None,current,PurePosixPath(path).name
        else:
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,fd)
            current=root
            for part in PurePosixPath(path).parts[:-1]:
                if create:
                    try:os.mkdir(part,0o755,dir_fd=fd);os.fsync(fd)
                    except FileExistsError:pass
                fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);stack.callback(os.close,fd)
                current=current/part
            yield fd,current,PurePosixPath(path).name
        if directory_identity(root)!=before:raise CloudError('cloud_delivery_root_changed')


def lstat(fd,directory,name):
    return os.stat(name,dir_fd=fd,follow_symlinks=False) if fd is not None else (directory/name).lstat()


def rename_new(source,destination,*,dir_fd=None):
    """Atomic exclusive rename, including an existing empty directory."""
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.MoveFileExW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint]
        if not kernel.MoveFileExW(str(source),str(destination),0):raise ctypes.WinError(ctypes.get_last_error())
    elif sys.platform=='darwin':
        libc=ctypes.CDLL(None,use_errno=True)
        libc.renameatx_np.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
        descriptor=dir_fd if dir_fd is not None else -2
        if libc.renameatx_np(descriptor,os.fsencode(source),descriptor,os.fsencode(destination),4):raise OSError(ctypes.get_errno(),'exclusive rename failed')
    else:
        libc=ctypes.CDLL(None,use_errno=True)
        libc.renameat2.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
        descriptor=dir_fd if dir_fd is not None else -100
        if libc.renameat2(descriptor,os.fsencode(source),descriptor,os.fsencode(destination),1):raise OSError(ctypes.get_errno(),'exclusive rename failed')


def replace_entry(fd,directory,name,source,entry,*,validate,create):
    temporary='.nyt-delivery-'+uuid4().hex
    kw={'dir_fd':fd} if fd is not None else {}
    target=temporary if fd is not None else directory/temporary
    try:
        if entry.type=='symlink':os.symlink(entry.target,target,**kw)
        else:
            descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,entry.mode,**kw)
            with os.fdopen(descriptor,'wb') as out,source.open('rb') as data:
                while chunk:=data.read(262144):out.write(chunk)
                out.flush();os.fsync(out.fileno())
                os.chmod(target,entry.mode,**kw)
        if not validate():raise CloudError('cloud_delivery_conflict')
        if create:
            try:rename_new(temporary if fd is not None else directory/temporary,
                name if fd is not None else directory/name,dir_fd=fd)
            except FileExistsError:raise CloudError('cloud_delivery_conflict') from None
        elif fd is not None:os.replace(temporary,name,src_dir_fd=fd,dst_dir_fd=fd)
        else:os.replace(directory/temporary,directory/name)
        if fd is not None:os.fsync(fd)
    finally:
        try:os.unlink(target,**kw)
        except FileNotFoundError:pass
