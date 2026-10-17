"""Portable, noninheritable regular-file handles and local ownership locks."""

import errno
import os
from pathlib import Path
import stat


def _regular(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def check_store_file(path):
    if not _regular(Path(path).lstat()):
        raise OSError(errno.EINVAL, 'unsafe_store_file')


def _windows_open(path, exclusive_create):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileInformation(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                    ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000 | 0x40000000, 0x1 | 0x2,
                                None, 1 if exclusive_create else 4, 0x00200000 | 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileInformation()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10) or info.links != 1:
            raise OSError(errno.EINVAL, 'unsafe_store_file')
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return fd  # CRT owns the Win32 handle from here.


def open_store_file(path, *, exclusive_create=False):
    path = Path(path)
    if os.name == 'nt':
        fd = _windows_open(path, exclusive_create)
    else:
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        fd = os.open(path, flags | (os.O_EXCL if exclusive_create else 0), 0o600)
    try:
        os.set_inheritable(fd, False)
        info, named = os.fstat(fd), path.lstat()
        if not _regular(info) or not _regular(named) or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise OSError(errno.EINVAL, 'unsafe_store_file')
    except BaseException:
        os.close(fd)
        raise
    return fd


def acquire_store_lock(fd, *, blocking=False):
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            raise BlockingIOError(error.errno, 'storage_in_use') from error
        raise


def release_store_lock(fd):
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
