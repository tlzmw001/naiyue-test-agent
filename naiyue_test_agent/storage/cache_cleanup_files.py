"""Directory custody and identity-checked operations for local maintenance."""
from contextlib import ExitStack, contextmanager
import errno
import os
from pathlib import Path
import stat

from .files import open_store_readonly, windows_api_path


class CleanupIssue(Exception):
    def __init__(self, code, reason, *, skipped=True):
        super().__init__(reason)
        self.code, self.skipped = code, skipped


@contextmanager
def cleanup_step(path, step):
    try:
        yield
    except (OSError, CleanupIssue) as error:
        error.cleanup_path = path
        error.cleanup_step = step
        raise


def identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def regular(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@contextmanager
def pinned_directory(path):
    path = Path(path).absolute()
    with ExitStack() as stack:
        if os.name == 'nt':
            from naiyue_test_agent.storage.windows_directory import directory_handle, signature
            for parent in (*reversed(path.parents), path):
                signature(stack.enter_context(directory_handle(parent)))
            yield Directory(Path(windows_api_path(path)), None)
            return
        fd = None
        for parent in (*reversed(path.parents), path):
            fd = os.open(parent if fd is None else parent.name,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
        directory = Directory(path, fd)
        directory.verify()
        yield directory


class Directory:
    def __init__(self, path, fd):
        self.path, self.fd = path, fd

    def verify(self):
        if self.fd is not None:
            observed = self.path.lstat()
            if not stat.S_ISDIR(observed.st_mode) or identity(observed)[:2] != identity(os.fstat(self.fd))[:2]:
                raise CleanupIssue('identity_changed', '目录身份已变化')

    def entries(self):
        return os.scandir(self.fd if self.fd is not None else self.path)

    def info(self, name):
        if self.fd is None:
            return (self.path / name).lstat()
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def read(self, name, limit):
        self.verify()
        before = self.info(name)
        if not regular(before):
            raise CleanupIssue('unexpected_entry', '条目不是受管普通文件')
        if self.fd is None:
            fd = open_store_readonly(self.path / name)
        else:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not regular(opened) or identity(opened) != identity(before):
                raise CleanupIssue('identity_changed', '读取前文件已变化')
            data = stream.read(limit + 1)
            if len(data) > limit or identity(os.fstat(stream.fileno())) != identity(before):
                raise CleanupIssue('metadata_unavailable', '元数据超限或已变化', skipped=False)
        return data

    def unlink(self, name, expected):
        self.verify()
        found = self.info(name)
        if not regular(found) or identity(found) != expected:
            raise CleanupIssue('identity_changed', '文件身份已变化')
        if self.fd is None:
            (self.path / name).unlink()
        else:
            os.unlink(name, dir_fd=self.fd)
        try:
            self.info(name)
        except FileNotFoundError:
            return
        raise CleanupIssue('identity_changed', '删除后路径已被重新占用', skipped=False)

    def sync(self):
        if self.fd is not None:
            os.fsync(self.fd)


def error_code(error):
    if isinstance(error, CleanupIssue):
        return error.code
    if isinstance(error, FileNotFoundError):
        return 'already_missing'
    if getattr(error, 'winerror', None) in (32, 33):
        return 'sharing_violation'
    if isinstance(error, PermissionError):
        return 'permission_denied'
    if getattr(error, 'errno', None) in (errno.ELOOP, errno.ENOTDIR):
        return 'ownership_invalid'
    return 'io_error'
