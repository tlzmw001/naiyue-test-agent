"""Private B root and noninherited single-owner lock; no A data discovery."""
import json
import logging
import os
from pathlib import Path
import stat
from uuid import uuid4

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud.schema import private_file
from naiyue_test_agent.storage.files import (acquire_store_lock, release_store_lock,
    check_store_directory, open_store_file, open_store_readonly, sync_store_directory)

logger = logging.getLogger(__name__)
ROOT_FORMAT = 'naiyue-cloud-root'


def private_directory(path):
    check_store_directory(path)
    info = path.lstat()
    if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise CloudError('cloud_storage_not_private', 503)


class CloudOwner:
    def __init__(self, settings):
        self.settings, self.fd = settings, None
        self.root = settings.data_dir

    def __enter__(self):
        try:
            root = self.root
            for protected in self.settings.protected_roots:
                protected = Path(protected).expanduser().absolute()
                if root.is_relative_to(protected) or protected.is_relative_to(root):
                    raise CloudError('cloud_protected_root', 503)
            parent = root.parent
            while not parent.exists():
                parent = parent.parent
            check_store_directory(parent)
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            private_directory(root)
            marker = root / 'cloud-root.json'
            if not marker.exists() and any(root.iterdir()):
                raise CloudError('cloud_foreign_root', 503)
            if marker.exists():
                self.record = self._read_marker()
            else:
                self.record = {'format': ROOT_FORMAT, 'schema': 1, 'instance_id': str(uuid4()), 'status': 'initializing'}
            lock_path = root / 'owner.lock'
            if lock_path.exists():
                private_file(lock_path)
            self.fd = open_store_file(lock_path)
            try:
                acquire_store_lock(self.fd)
            except BlockingIOError:
                raise CloudError('cloud_in_use', 503) from None
            if marker.exists():
                self.record = self._read_marker()
            else:
                self._write_marker(marker, exclusive=True)
            # Only Coordinator management paths; never recurse through task data.
            history = root / 'history'
            history.mkdir(mode=0o700, exist_ok=True)
            private_directory(history)
            return self
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            if isinstance(error, OSError):
                logger.error('cloud_root_open_failed error_type=%s', type(error).__name__)
                raise CloudError('cloud_storage_unavailable', 503) from error
            raise

    def _read_marker(self):
        marker = self.root / 'cloud-root.json'
        private_file(marker)
        with os.fdopen(open_store_readonly(marker), 'rb') as stream:
            content = stream.read(4097)
        try:
            value = json.loads(content)
            if (len(content) > 4096 or set(value) != {'format', 'schema', 'instance_id', 'status'}
                    or value['format'] != ROOT_FORMAT or type(value['schema']) is not int or value['schema'] not in (1,2)
                    or value['status'] not in ('initializing', 'ready')
                    or str(__import__('uuid').UUID(value['instance_id'])) != value['instance_id']):
                raise ValueError('invalid_root_marker')
        except (ValueError, TypeError, KeyError, AttributeError):
            raise CloudError('cloud_invalid_root', 503) from None
        return value

    def _write_marker(self, path, *, exclusive=False):
        fd = open_store_file(path, exclusive_create=exclusive)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json.dumps(self.record).encode())
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        sync_store_directory(self.root)

    def ready(self):
        if self.record['status'] == 'ready':
            return
        self.record['status'] = 'ready'
        temporary = self.root / '.cloud-root.next'
        if temporary.exists():
            private_file(temporary)
        self._write_marker(temporary)
        os.replace(temporary, self.root / 'cloud-root.json')
        sync_store_directory(self.root)

    def enable_guards(self):
        if self.record['schema']==2:return
        self.record=self.record|dict(schema=2)
        temporary=self.root/'.cloud-root.next'
        if temporary.exists():private_file(temporary)
        self._write_marker(temporary)
        os.replace(temporary,self.root/'cloud-root.json')
        sync_store_directory(self.root)

    def __exit__(self, kind, error, traceback):
        if self.fd is not None:
            try:
                release_store_lock(self.fd)
            except OSError as failure:
                logger.error('cloud_unlock_failed error_type=%s', type(failure).__name__)
                if error is None:
                    raise
            finally:
                os.close(self.fd)
                self.fd = None
