"""Descriptor-relative file access beneath the registered workspace root."""
from collections import deque
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import logging
import os
from pathlib import Path, PurePosixPath
import stat
import weakref

from langchain_core.tools import ToolException

logger = logging.getLogger(__name__)


class WorkspaceOutside(ToolException):
    """Distinct boundary reason for resource APIs and model-facing tools."""


def _close(fd):
    try:
        os.close(fd)
    except OSError as error:
        logger.warning('envd_workspace_close_failed error_type=%s', type(error).__name__)


@dataclass
class OpenTarget:
    fd: int | None
    parent: int
    name: str
    relative: str
    parent_relative: str
    info: os.stat_result | None
    linked: bool


class Workspace:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.physical = self.root.resolve(strict=True)
        self.fd = os.open(self.physical, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.identity = os.fstat(self.fd)
        self._finalizer = weakref.finalize(self, _close, self.fd)

    def close(self):
        self._finalizer()

    def parts(self, path):
        value = PurePosixPath(path)
        if '\0' in str(path):
            raise ToolException('File target is unavailable.')
        if value.is_absolute():
            try:
                value = value.relative_to(self.root)
            except ValueError:
                # The physical spelling is needed for native Git and canonical subjects.
                try:
                    value = value.relative_to(self.physical)
                except ValueError:
                    raise WorkspaceOutside('File target is outside the sandbox execution root.') from None
        return list(value.parts)

    def public(self, relative):
        return str(self.root / relative)

    @contextmanager
    def open(self, path, *, missing=False, create_parents=False, owner=None):
        pending = deque(self.parts(path))
        names, descriptors, links = [], [self.fd], 0
        with ExitStack() as stack:
            while pending:
                name = pending.popleft()
                if name in ('', '.'):
                    continue
                if name == '..':
                    if not names:
                        raise WorkspaceOutside('File target is outside the sandbox execution root.')
                    names.pop(); descriptors.pop()
                    continue
                parent = descriptors[-1]
                try:
                    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    if pending and create_parents:
                        os.mkdir(name, 0o755, dir_fd=parent)
                        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        stack.callback(os.close, fd)
                        if owner is not None:
                            os.fchown(fd, *owner)
                        names.append(name); descriptors.append(fd)
                        continue
                    if not missing:
                        raise
                    tail = [name, *pending]
                    if '..' in tail:
                        raise ToolException('Missing file parent is unavailable.')
                    yield OpenTarget(None, parent, '/'.join(tail), '/'.join([*names,*tail]),
                                     '/'.join(names), None, bool(links))
                    return
                if stat.S_ISLNK(before.st_mode):
                    links += 1
                    if links > 40:
                        raise ToolException('File target is unavailable: link cycle or excessive links.')
                    target = os.readlink(name, dir_fd=parent)
                    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (before.st_dev,before.st_ino,before.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_ctime_ns):
                        raise ToolException('File target changed during link resolution.')
                    if PurePosixPath(target).is_absolute():
                        target_parts = self.parts(target)
                        names, descriptors = [], [self.fd]
                    else:
                        target_parts = list(PurePosixPath(target).parts)
                    pending.extendleft(reversed(target_parts))
                    continue
                directory = stat.S_ISDIR(before.st_mode)
                if (not directory and not stat.S_ISREG(before.st_mode) or
                        not directory and before.st_nlink != 1 or before.st_dev != self.identity.st_dev):
                    raise ToolException('File target is unavailable: unsupported type, mount or hard link.')
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if directory or pending:
                    flags |= os.O_DIRECTORY
                fd = os.open(name, flags, dir_fd=parent)
                stack.callback(os.close, fd)
                opened = os.fstat(fd)
                if (opened.st_dev,opened.st_ino) != (before.st_dev,before.st_ino):
                    raise ToolException('File target changed while opening.')
                if pending:
                    names.append(name); descriptors.append(fd)
                    continue
                yield OpenTarget(fd,parent,name,'/'.join([*names,name]),'/'.join(names),opened,bool(links))
                return
            fd = os.dup(descriptors[-1]); stack.callback(os.close,fd)
            yield OpenTarget(fd,descriptors[-1],'.','/'.join(names),'/'.join(names),os.fstat(fd),bool(links))

    def subject(self, path, operation):
        with self.open(path, missing=True) as target:
            kind = ('directory' if stat.S_ISDIR(target.info.st_mode) else 'file') if target.info else 'missing'
            if (kind != 'missing' and operation in ('read','write') and kind != 'file' or
                    kind != 'missing' and operation in ('list','find') and kind != 'directory'):
                raise ToolException('File target has an unavailable file type.')
            parent = os.fstat(target.parent)
            return dict(operation=operation,workspace=str(self.root),
                requested=str(self.root/str(path)) if not Path(path).is_absolute() else str(path),
                resolved=self.public(target.relative),kind=kind,
                anchor=dict(path=self.public(target.parent_relative),device=parent.st_dev,inode=parent.st_ino))

    def check(self, target, subject):
        if self.public(target.relative) != subject['resolved']:
            raise ToolException('File target changed after approval review.')
        if target.info is not None:
            kind = 'directory' if stat.S_ISDIR(target.info.st_mode) else 'file'
            if subject['kind'] != 'missing' and kind != subject['kind']:
                raise ToolException('File target type changed after approval review.')
        anchor = subject['anchor']
        with self.open(anchor['path']) as parent:
            if (parent.info.st_dev,parent.info.st_ino) != (anchor['device'],anchor['inode']):
                raise ToolException('File parent changed after approval review.')

