"""Delete only fixed managed entries, anchored to no-follow directory FDs."""
import os
import stat
import time
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from .resource_disk import open_directory, mount_id


def remove_task_data(root, task_id, limits):
    from pydantic import TypeAdapter
    from naiyue_test_agent.contracts.cloud_tasks import TaskId
    TypeAdapter(TaskId).validate_python(task_id)
    deadline = time.monotonic() + limits.scan_timeout_seconds
    count = 0

    def check(depth):
        nonlocal count
        count += 1
        if count > limits.scan_max_entries or depth > limits.scan_max_depth or time.monotonic() >= deadline:
            raise CloudError('task_delete_cleanup_budget', 503)

    def verified(fd, device, mount):
        if os.fstat(fd).st_dev != device or mount_id(fd) != mount:
            raise CloudError('task_delete_mount', 503)

    def remove(parent, name, device, mount, depth):
        check(depth)
        try: info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError: return
        if stat.S_ISDIR(info.st_mode):
            child = open_directory(name, dir_fd=parent)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise CloudError('task_delete_path_changed', 503)
                verified(child, device, mount)
                with os.scandir(child) as entries:
                    for entry in entries: remove(child, entry.name, device, mount, depth + 1)
                os.fsync(child)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                    raise CloudError('task_delete_path_changed', 503)
                os.rmdir(name, dir_fd=parent)
            finally: os.close(child)
        else:
            if info.st_dev != device: raise CloudError('task_delete_mount', 503)
            os.unlink(name, dir_fd=parent)
        os.fsync(parent)

    fd = open_directory(root)
    try:
        device, mount = os.fstat(fd).st_dev, mount_id(fd)
        for kind in ('tasks', 'artifacts', 'artifact-transfers', 'history'):
            try: base = open_directory(kind, dir_fd=fd)
            except FileNotFoundError: continue
            try:
                verified(base, device, mount)
                names = [task_id] if kind != 'history' else [task_id + '.sqlite' + suffix
                    for suffix in ('', '-wal', '-shm', '-journal', '.lock')]
                for name in names: remove(base, name, device, mount, 0)
            finally: os.close(base)
    finally: os.close(fd)
