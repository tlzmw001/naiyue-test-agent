"""Real file ownership and the platform dispatch preserve IO failures."""

import os

import pytest


def test_lock_is_nonblocking_and_released_on_close(tmp_path):
    from naiyue_test_agent.storage.files import open_store_file, acquire_store_lock, release_store_lock
    path = tmp_path / 'owner.lock'
    first, second = open_store_file(path), open_store_file(path)
    try:
        assert not os.get_inheritable(first)
        acquire_store_lock(first)
        with pytest.raises(BlockingIOError):
            acquire_store_lock(second)
        release_store_lock(first)
        acquire_store_lock(second)
        release_store_lock(second)
    finally:
        os.close(first)
        os.close(second)


def test_regular_files_keep_permissions_and_links_are_rejected(tmp_path):
    from naiyue_test_agent.storage.files import open_store_file, check_store_file
    target = tmp_path / 'regular'
    target.write_bytes(b'content')
    target.chmod(0o644)
    check_store_file(target)
    fd = open_store_file(target)
    os.close(fd)
    assert target.read_bytes() == b'content'
    alias = tmp_path / 'alias'
    alias.symlink_to(target)
    with pytest.raises(OSError):
        open_store_file(alias)
    with pytest.raises(OSError):
        check_store_file(alias)


def test_windows_lock_dispatch_uses_fixed_range_and_preserves_other_io(monkeypatch):
    import errno
    from types import SimpleNamespace
    import sys
    from naiyue_test_agent.storage import files as storage_files
    calls = []
    monkeypatch.setattr(storage_files, 'os', SimpleNamespace(name='nt', SEEK_SET=0,
        lseek=lambda *args: calls.append(('seek', *args))))
    windows = SimpleNamespace(LK_NBLCK=2, LK_LOCK=1, LK_UNLCK=0,
        locking=lambda *args: calls.append(('lock', *args)))
    monkeypatch.setitem(sys.modules, 'msvcrt', windows)
    storage_files.acquire_store_lock(9)
    storage_files.release_store_lock(9)
    assert calls == [('seek', 9, 0, 0), ('lock', 9, 2, 1), ('seek', 9, 0, 0), ('lock', 9, 0, 1)]
    def fail(*_args):
        raise OSError(errno.EIO, 'synthetic_io_failure')
    windows.locking = fail
    with pytest.raises(OSError) as caught:
        storage_files.acquire_store_lock(9)
    assert not isinstance(caught.value, BlockingIOError)
    assert caught.value.errno == errno.EIO


def test_windows_open_uses_reparse_handle_and_noninherit_crt_flags(monkeypatch, tmp_path):
    import ctypes
    from types import SimpleNamespace
    import sys
    from naiyue_test_agent.storage import files as storage_files
    calls = []
    def create(*args):
        calls.append(('create', args))
        return 456
    def information(handle, pointer):
        pointer._obj.attributes = 0
        pointer._obj.links = 1
        return True
    def close(handle):
        calls.append(('close', handle))
        return True
    kernel = SimpleNamespace(CreateFileW=create, GetFileInformationByHandle=information, CloseHandle=close)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(storage_files, 'os', SimpleNamespace(O_RDWR=2, O_BINARY=0x8000, O_NOINHERIT=0x80))
    def convert(handle, flags):
        calls.append(('convert', handle, flags))
        return 9
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(open_osfhandle=convert))
    assert storage_files._windows_open(tmp_path / 'owner', False) == 9
    args = calls[0][1]
    assert args[3] is None  # Noninheritable native handle.
    assert args[4] == 4 and args[5] & 0x00200000  # OPEN_ALWAYS / OPEN_REPARSE_POINT.
    assert calls[1] == ('convert', 456, 2 | 0x8000 | 0x80)
    assert not any(item[0] == 'close' for item in calls)


def test_windows_handle_is_closed_when_crt_conversion_fails(monkeypatch, tmp_path):
    import ctypes
    from types import SimpleNamespace
    import sys
    from naiyue_test_agent.storage import files as storage_files
    closed = []
    def create(*args):
        return 456
    def information(handle, pointer):
        pointer._obj.attributes = 0
        pointer._obj.links = 1
        return True
    def close(handle):
        closed.append(handle)
        return True
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **kw: SimpleNamespace(
        CreateFileW=create, GetFileInformationByHandle=information, CloseHandle=close), raising=False)
    monkeypatch.setattr(storage_files, 'os', SimpleNamespace(O_RDWR=2, O_BINARY=0x8000, O_NOINHERIT=0x80))
    original = OSError('synthetic_crt_failure')
    def fail(*args):
        raise original
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(open_osfhandle=fail))
    with pytest.raises(OSError) as caught:
        storage_files._windows_open(tmp_path / 'owner', False)
    assert caught.value is original and closed == [456]
