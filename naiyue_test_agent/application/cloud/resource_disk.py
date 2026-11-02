"""FD-anchored metadata-only measurement; never open task files or follow links."""
import os
import stat
import sys
import time

from naiyue_test_agent.contracts.cloud_resources import DiskUsage
from naiyue_test_agent.contracts.cloud_tasks import CloudError

def open_directory(path, *, dir_fd=None):
    if os.name == 'nt':
        raise OSError('resource_scan_platform_unsupported')
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)


def mount_id(fd):
    if sys.platform != 'linux':
        return os.fstat(fd).st_dev
    # Kernel FD identity distinguishes same-device bind mounts as well.
    with open('/proc/self/fdinfo/' + str(fd), 'rb') as stream:
        value = stream.read(4097)
    if len(value) > 4096:
        raise OSError('fdinfo_budget')
    for line in value.splitlines():
        if line.startswith(b'mnt_id:'):
            return int(line.split(b':', 1)[1])
    raise OSError('fdinfo_mount_missing')


def free_bytes(root):
    fd = open_directory(root)
    try:
        value = os.fstatvfs(fd)
        return value.f_bavail * value.f_frsize
    finally:
        os.close(fd)


def measure(root, limits):
    entries = logical = 0
    available = None
    deadline = time.monotonic() + limits.scan_timeout_seconds

    def check():
        if entries > limits.scan_max_entries or time.monotonic() >= deadline:
            raise CloudError('resource_scan_budget', 503)

    def visit(fd, device, mount, depth):
        nonlocal entries, logical
        check()
        if depth > limits.scan_max_depth:
            raise CloudError('resource_scan_budget', 503)
        if os.fstat(fd).st_dev != device or mount_id(fd) != mount:
            raise CloudError('resource_scan_mount', 503)
        with os.scandir(fd) as iterator:
            for item in iterator:
                entries += 1
                check()
                info = item.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = open_directory(item.name, dir_fd=fd)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise CloudError('resource_scan_changed', 503)
                        visit(child, device, mount, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    logical += info.st_size
        check()

    try:
        fd = open_directory(root)
        try:
            fact = os.fstat(fd)
            volume = os.fstatvfs(fd)
            available = volume.f_bavail * volume.f_frsize
            visit(fd, fact.st_dev, mount_id(fd), 0)
        finally:
            os.close(fd)
    except (OSError, ValueError, CloudError) as error:
        return DiskUsage(status='unknown', free_bytes=available, entries=entries, checked_at=time.time(),
            reason=error.code if isinstance(error, CloudError) else 'resource_scan_unavailable')
    return DiskUsage(status='known', logical_bytes=logical, free_bytes=available,
        entries=entries, checked_at=time.time())
