"""A root-scoped lease shared by service and standalone client readers."""
from contextlib import contextmanager
import json
import os
from pathlib import Path

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue, pinned_directory
from naiyue_test_agent.storage.files import open_store_file

MARKER = {'format': 'nyt-input-cache', 'schema': 1}
SIGNATURE = b'nyt-input-cache-init-v1\n'


def validate_cache(directory):
    if json.loads(directory.read('cache.json', 256)) != MARKER:
        raise CleanupIssue('ownership_invalid', '输入缓存所有权标记不匹配')


def windows_lock(fd, exclusive):
    import ctypes
    from ctypes import wintypes as w
    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                    ('offset', w.DWORD), ('offset_high', w.DWORD), ('event', w.HANDLE)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    lock = kernel.LockFileEx
    lock.argtypes = (w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped))
    lock.restype = w.BOOL
    unlock = kernel.UnlockFileEx
    unlock.argtypes = (w.HANDLE, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped))
    unlock.restype = w.BOOL
    handle, offset = msvcrt.get_osfhandle(fd), Overlapped()
    if not lock(handle, 3 if exclusive else 1, 0, 1, 0, ctypes.byref(offset)):
        code = ctypes.get_last_error()
        if code == 33:
            raise BlockingIOError(code, 'input_cache_busy')
        raise ctypes.WinError(code)

    def release():
        if not unlock(handle, 0, 1, 0, ctypes.byref(offset)):
            raise ctypes.WinError(ctypes.get_last_error())
    return release


@contextmanager
def readers(directory, *, exclusive=False):
    fd = open_store_file(directory.path / 'readers.lock')
    try:
        if os.name == 'nt':
            release = windows_lock(fd, exclusive)
        else:
            import fcntl
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            def release():
                fcntl.flock(fd, fcntl.LOCK_UN)
        try:
            yield
        finally:
            release()
    finally:
        os.close(fd)


@contextmanager
def package_reader(path, *, missing_ok=False):
    path = Path(path).absolute()
    root = path.parent.parent
    managed = path.parent.name == 'inputs' and (
        root.name == 'cloud-input-cache' or (root / 'cache.json').exists() or (root / 'cache.json').is_symlink())
    if not managed:
        yield
        return
    if missing_ok and not root.exists() and not root.is_symlink():
        yield
        return
    with pinned_directory(root) as directory:
        try:
            validate_cache(directory)
        except FileNotFoundError as error:
            raise CloudError('input_cache_invalid') from error
        except (CleanupIssue, ValueError) as error:
            raise CloudError('input_cache_invalid') from error
        from contextlib import ExitStack
        with ExitStack() as stack:
            try:
                stack.enter_context(readers(directory))
            except BlockingIOError as error:
                raise CloudError('input_cache_busy') from error
            if not missing_ok:
                try:
                    with pinned_directory(path):
                        pass
                except FileNotFoundError as error:
                    raise CloudError('input_cache_expired', 410) from error
            yield
