"""One application-owned connection to a shared local personal memory store."""

import logging
import os
from pathlib import Path
import sqlite3
from threading import RLock

from naiyue_test_agent.storage_files import check_store_directory, check_store_file, open_store_file
from . import memory_lexical, memory_schema, memory_lifecycle_schema
from .memory_queries import MemoryReadError


logger = logging.getLogger(__name__)
APPLICATION_ID = 0x4E59544D


class PersonalMemory:
    def __init__(self, path, settings):
        self.path = Path(path).absolute()
        self.connection = None
        self._lock = RLock()
        try:
            missing, directory = [], self.path.parent
            while not directory.exists():
                missing.append(directory)
                directory = directory.parent
            check_store_directory(directory)
            for directory in reversed(missing):
                directory.mkdir(mode=0o700, exist_ok=True)
                check_store_directory(directory)
            fd = open_store_file(self.path)
            os.close(fd)
            for suffix in ('-wal', '-shm', '-journal'):
                try:check_store_file(self.path.with_name(self.path.name + suffix))
                except FileNotFoundError:pass
            connection = self.connection = sqlite3.connect(self.path, check_same_thread=False,
                timeout=settings.busy_timeout_ms / 1000)
            connection.row_factory = sqlite3.Row
            memory_lexical.configure(connection)
            mode = connection.execute('PRAGMA journal_mode').fetchone()[0]
            if mode != 'delete':
                raise MemoryReadError('memory_personal_mode_unavailable')
            connection.execute('PRAGMA synchronous=FULL')
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                objects = connection.execute('SELECT name FROM sqlite_master').fetchall()
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                application = connection.execute('PRAGMA application_id').fetchone()[0]
                if not objects and version == 0 and application == 0:
                    memory_schema.create(connection)
                    memory_lifecycle_schema.upgrade(connection)
                    connection.execute('PRAGMA user_version=2')
                    connection.execute(f'PRAGMA application_id={APPLICATION_ID}')
                elif version not in (1,2) or application != APPLICATION_ID:
                    raise ValueError('memory_invalid_format')
                elif version == 1:
                    from .memory_personal_upgrade import backup_format1
                    memory_schema.check(connection,version=1)
                    backup = backup_format1(self.path)
                    logger.info('memory_personal_upgrade_backup_created backup_id=%s',backup.name)
                    memory_lifecycle_schema.upgrade(connection)
                    connection.execute('PRAGMA user_version=2')
                memory_schema.check(connection)
            connection.execute('PRAGMA synchronous=FULL')
        except BaseException as error:
            logger.error('memory_personal_open_failed error_type=%s', type(error).__name__)
            try:
                self.close()
            except BaseException as closing_error:
                logger.error('memory_personal_cleanup_secondary_failed error_type=%s', type(closing_error).__name__)
            if isinstance(error, ValueError) and not isinstance(error, MemoryReadError):
                raise MemoryReadError('memory_invalid_format') from error
            raise

    def close(self):
        with self._lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
