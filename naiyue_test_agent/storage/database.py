"""Application SQLite connection, transaction and resource ownership."""

import logging
import os
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4


from naiyue_test_agent.application.errors import SessionPersistenceError, StorageStartupError
from .migrations import initialize_store
from .backup_schema import CURRENT_SCHEMA
from .maintenance import check_restore_marker
from .files import acquire_store_lock, check_store_file, open_store_file, release_store_lock
from naiyue_test_agent.application.conversation import history_projection
from naiyue_test_agent.application.memory import memory_lexical


logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path):
        self._initialize_owner(Path(path).expanduser().absolute())
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            lock_path = self.path.with_name(self.path.name + '.lock')
            fd = open_store_file(lock_path)
            self._stack.callback(os.close, fd)
            try:
                acquire_store_lock(fd)
            except BlockingIOError as error:
                raise SessionPersistenceError('storage_in_use') from error
            self._stack.callback(release_store_lock, fd)
            check_restore_marker(self.path)
            try:
                check_store_file(self.path)
                existing = True
            except FileNotFoundError:
                existing = False
                file_fd = open_store_file(self.path, exclusive_create=True)
                os.close(file_fd)
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = self.path.with_name(self.path.name + suffix)
                try:
                    check_store_file(sidecar)
                except FileNotFoundError:
                    pass
            self.connection = sqlite3.connect(self.path, check_same_thread=False)
            self._stack.callback(self.connection.close)
            self.connection.row_factory = sqlite3.Row
            history_projection.configure_connection(self.connection)
            memory_lexical.configure(self.connection)
            initialize_store(self.connection, self.path, existing=existing)
        except BaseException as error:
            logger.error('session_storage_open_failed error_type=%s', type(error).__name__)
            try:
                self.close()
            except BaseException as closing_error:
                logger.error('session_storage_cleanup_secondary_failed error_type=%s', type(closing_error).__name__)
            if isinstance(error, SessionPersistenceError):
                raise
            if isinstance(error, ValueError) and str(error) == 'restore_incomplete':
                raise StorageStartupError('restore_incomplete', database_path=self.path, phase='restore_guard',
                    target_schema=CURRENT_SCHEMA) from error
            if isinstance(error, (OSError, sqlite3.Error)):
                raise SessionPersistenceError('storage_unavailable') from error
            raise


    def _initialize_owner(self, path):
        self.path, self.uri = path, None
        self._stack = ExitStack()
        self.lock = RLock()
        self._closed = self._active = False

    @classmethod
    def open_memory(cls):
        database = cls.__new__(cls)
        database._initialize_owner(None)
        database.uri = f'file:/naiyue-{uuid4().hex}?vfs=memdb'
        try:
            database.connection = sqlite3.connect(database.uri, uri=True, check_same_thread=False)
            database._stack.callback(database.connection.close)
            database.connection.row_factory = sqlite3.Row
            history_projection.configure_connection(database.connection)
            memory_lexical.configure(database.connection)
            initialize_store(database.connection, None, existing=False)
        except BaseException as error:
            logger.error('memory_storage_open_failed error_type=%s', type(error).__name__)
            try:
                database.close()
            except BaseException as closing_error:
                logger.error('memory_storage_cleanup_secondary_failed error_type=%s', type(closing_error).__name__)
            if isinstance(error, (OSError, sqlite3.Error)):
                raise SessionPersistenceError('storage_unavailable') from error
            raise
        return database

    @classmethod
    def open_personal(cls, path, settings):
        from .personal_database import initialize_personal
        database = cls.__new__(cls)
        database._initialize_owner(Path(path).absolute())
        try:
            initialize_personal(database, settings)
        except BaseException as error:
            logger.error('memory_personal_open_failed error_type=%s', type(error).__name__)
            try:
                database.close()
            except BaseException as closing_error:
                logger.error('memory_personal_cleanup_secondary_failed error_type=%s', type(closing_error).__name__)
            from naiyue_test_agent.application.memory.memory_queries import MemoryReadError
            if isinstance(error, ValueError) and not isinstance(error, MemoryReadError):
                raise MemoryReadError('memory_invalid_format') from error
            raise
        return database

    @contextmanager
    def transaction(self, *, write=False, error_code=None):
        with self.lock:
            if self._active:
                raise RuntimeError('nested_database_transaction')
            try:
                nested = self.connection.in_transaction
            except sqlite3.Error as primary:
                if error_code is not None:
                    logger.error('database_transaction_failed code=%s error_type=%s', error_code, type(primary).__name__)
                    raise SessionPersistenceError(error_code) from primary
                raise
            if nested:
                raise RuntimeError('nested_database_transaction')
            self._active = True
            try:
                self.connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield self.connection
                self.connection.commit()
            except BaseException as primary:
                try:
                    self.connection.rollback()
                except BaseException as error:
                    logger.error('database_rollback_secondary_failed error_type=%s', type(error).__name__)
                if error_code is not None and isinstance(primary, sqlite3.Error):
                    logger.error('database_transaction_failed code=%s error_type=%s', error_code, type(primary).__name__)
                    raise SessionPersistenceError(error_code) from primary
                raise
            finally:
                self._active = False

    def read_rows(self, sql, values=()):
        from .queries import read_rows
        with self.transaction(error_code='session_read_failed') as connection:
            return read_rows(connection, sql, values)

    def close(self):
        with self.lock:
            if not self._closed:
                self._closed = True
                try:
                    self._stack.close()
                except (OSError, sqlite3.Error) as error:
                    logger.error('session_storage_close_failed error_type=%s', type(error).__name__)
                    raise SessionPersistenceError('storage_close_failed') from error

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            self.close()
        except SessionPersistenceError:
            if error is None:
                raise
