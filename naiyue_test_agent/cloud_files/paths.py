"""Pinned source handles and portable whole-tree link validation."""
from contextlib import contextmanager,ExitStack
import os
from pathlib import PurePosixPath
import stat

from naiyue_test_agent.storage.files import check_store_directory
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def identity(info):return tuple(getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns'))


def _windows_source_open(path):
    # Source files may have multiple hard links. The management-store opener
    # deliberately rejects them and belongs to the separately pinned A runtime.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    class Information(ctypes.Structure):
        _fields_=[('attributes',wintypes.DWORD),('created',wintypes.FILETIME),
            ('accessed',wintypes.FILETIME),('written',wintypes.FILETIME),('volume',wintypes.DWORD),
            ('size_high',wintypes.DWORD),('size_low',wintypes.DWORD),('links',wintypes.DWORD),
            ('index_high',wintypes.DWORD),('index_low',wintypes.DWORD)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes=[wintypes.HANDLE,ctypes.POINTER(Information)]
    kernel.GetFileInformationByHandle.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
    handle=kernel.CreateFileW(str(path),0x80000000,0x1|0x2,None,3,0x00200000|0x80,None)
    if handle==wintypes.HANDLE(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:
        info=Information()
        if not kernel.GetFileInformationByHandle(handle,ctypes.byref(info)):raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400|0x10) or info.links<1:raise OSError('unsafe_source_file')
        return msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY|os.O_NOINHERIT)
    except BaseException:
        kernel.CloseHandle(handle);raise


@contextmanager
def source_handle(root,path,expected):
    target=root/path
    with ExitStack() as stack:
        if os.open in os.supports_dir_fd:
            parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,parent)
            for part in PurePosixPath(path).parts[:-1]:
                parent=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);stack.callback(os.close,parent)
            name=PurePosixPath(path).name;before=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if identity(before)!=expected:raise CloudError('input_source_changed')
            if stat.S_ISLNK(before.st_mode):
                yield os.readlink(name,dir_fd=parent).encode('utf-8'),None
                return
            if not stat.S_ISREG(before.st_mode):raise CloudError('input_type_unsupported')
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        else:
            check_store_directory(target.parent);before=target.lstat()
            if identity(before)!=expected:raise CloudError('input_source_changed')
            if stat.S_ISLNK(before.st_mode):
                yield os.readlink(target).encode('utf-8'),None
                return
            fd=_windows_source_open(target)
        stack.callback(os.close,fd);os.set_inheritable(fd,False);opened=os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino,opened.st_size)!=(before.st_dev,before.st_ino,before.st_size):
            raise CloudError('input_source_changed')
        stream=stack.enter_context(os.fdopen(os.dup(fd),'rb'))
        yield stream,opened
        if identity(os.fstat(fd))!=identity(opened):raise CloudError('input_source_changed')
        check_store_directory(target.parent)
        if identity(target.lstat())!=expected:raise CloudError('input_source_changed')


def check_links(entries):
    links={e.path:e.target for e in entries if e.type=='symlink'}
    known={e.path for e in entries}
    for path in links:
        pending=path.split('/');resolved=[];count=0
        while pending:
            part=pending.pop(0)
            if part in ('','.'):continue
            if part=='..':
                if not resolved:raise CloudError('input_link_outside')
                resolved.pop();continue
            current='/'.join([*resolved,part])
            if current in links:
                target=links[current];count+=1
                if (count>40 or not target or target.startswith('/') or '\\' in target or ':' in target
                        or any(ord(c)<32 for c in target)):
                    raise CloudError('input_link_outside')
                pending=target.split('/')+pending
            else:resolved.append(part)
        final='/'.join(resolved)
        if final not in known and not any(k.startswith(final+'/') for k in known):raise CloudError('input_link_unselected')
