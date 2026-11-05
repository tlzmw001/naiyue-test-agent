"""Only fixed task mount roots; never inspect untrusted task SQLite or recurse."""
import json
import os

from naiyue_test_agent.contracts.cloud_lifecycle import MountSpec
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud.schema import private_file
from naiyue_test_agent.storage.files import open_store_file, open_store_readonly, sync_store_directory
from .ownership import private_directory


class TaskDirectories:
    def __init__(self, root):
        self.root = root

    def prepare(self, task, template):
        if os.name == 'nt' or os.getuid() != template.uid or os.getgid() != template.gid:
            raise CloudError('task_directory_identity_mismatch')
        inputs = self.root / 'inputs' / task.input_id if task.input_id is not None else None
        if inputs is not None:private_directory(inputs)
        base = self.root / 'tasks'
        base.mkdir(mode=0o700, exist_ok=True)
        private_directory(base)
        parent = base / task.task_id
        parent.mkdir(mode=0o700, exist_ok=True)
        private_directory(parent)
        marker = parent / 'environment.json'
        expected = dict(task_id=task.task_id, project_id=task.project_id, input_id=task.input_id)
        try:
            private_file(marker)
        except FileNotFoundError:
            if any(parent.iterdir()): raise CloudError('task_directory_unowned')
            fd = open_store_file(marker, exclusive_create=True)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(expected | {'status': 'initializing'}).encode())
                stream.flush()
                os.fsync(stream.fileno())
            sync_store_directory(parent)
            initializing = True
        else:
            with os.fdopen(open_store_readonly(marker), 'rb') as stream:
                raw = stream.read(4097)
            try:
                value = json.loads(raw)
                if not isinstance(value, dict): raise ValueError('marker_not_object')
                status = value.pop('status')
                if len(raw) > 4096 or value != expected or status not in ('initializing', 'ready'):
                    raise ValueError('marker_identity_mismatch')
            except (ValueError, KeyError):
                raise CloudError('task_directory_identity_mismatch') from None
            initializing = status == 'initializing'
        mounts = []
        for name in ('workspace', 'state'):
            path = parent / name
            if initializing: path.mkdir(mode=0o700, exist_ok=True)
            private_directory(path)
            mounts.append(MountSpec(source=str(path), destination='/' + name, read_only=False))
        if inputs is not None:mounts.append(MountSpec(source=str(inputs), destination='/input', read_only=True))
        if initializing:
            temporary = parent / 'environment.next'
            if temporary.exists(): private_file(temporary)
            fd = open_store_file(temporary)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json.dumps(expected | {'status': 'ready'}).encode())
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, marker)
            sync_store_directory(parent)
        return mounts
