"""Independent formats opened through the existing Database owner."""
import os
import sqlite3
import stat

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.files import (acquire_store_lock, check_store_directory,
    check_store_file, open_store_file, release_store_lock)
from .history_schema import PROJECTION_SCHEMA
from .usage_schema import USAGE_SCHEMA

APPLICATION_IDS = {'registry': 0x4E594243, 'history': 0x4E594248}
SCHEMAS = {
    'registry': (
        'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        'CREATE TABLE projects(project_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)',
        '''CREATE TABLE tasks(task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
            generation INTEGER NOT NULL, revision INTEGER NOT NULL, record_json TEXT NOT NULL)''',
        '''CREATE TABLE commands(command_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
            resource_id TEXT NOT NULL, record_json TEXT NOT NULL)''',
        '''CREATE TABLE task_credentials(credential_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
            task_id TEXT NOT NULL REFERENCES tasks(task_id), project_id TEXT NOT NULL,
            generation INTEGER NOT NULL, scopes_json TEXT NOT NULL, expires_at REAL NOT NULL, revoked_at REAL)'''),
    'history': (
        'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        '''CREATE TABLE events(seq INTEGER PRIMARY KEY, source_generation INTEGER NOT NULL,
            run_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
            digest TEXT NOT NULL, received_at REAL NOT NULL)''')}


def private_file(path):
    check_store_file(path)
    info = path.lstat()
    if os.name != 'nt' and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise CloudError('cloud_storage_not_private', 503)


def _validate(connection, profile):
    actual = {row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")}
    version=connection.execute('PRAGMA user_version').fetchone()[0]
    expected=set(SCHEMAS[profile])
    if profile=='history' and version in (2,3):expected.update(PROJECTION_SCHEMA)
    if profile=='history' and version==3:expected.update(USAGE_SCHEMA)
    if (connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_IDS[profile]
            or version not in ((1,2,3) if profile=='history' else (1,))
            or actual != expected):
        raise CloudError('cloud_invalid_format', 503)


def initialize_cloud(database, profile, *, initialize):
    if profile not in SCHEMAS:
        raise ValueError('cloud_invalid_profile')
    check_store_directory(database.path.parent)
    if not initialize and (not database.path.exists() or not database.path.stat().st_size):
        raise CloudError('cloud_invalid_format', 503)
    for suffix in ('', '-wal', '-shm', '-journal', '.lock'):
        try:
            private_file(database.path.with_name(database.path.name + suffix))
        except FileNotFoundError:
            pass
    fd = open_store_file(database.path.with_name(database.path.name + '.lock'))
    database._stack.callback(os.close, fd)
    acquire_store_lock(fd)
    database._stack.callback(release_store_lock, fd)
    if database.path.exists() and database.path.stat().st_size:
        # Validate without setting WAL or running another product's migrations.
        connection = sqlite3.connect(database.path.as_uri() + '?mode=ro', uri=True)
        try:
            _validate(connection, profile)
        finally:
            connection.close()
    fd = open_store_file(database.path)
    os.close(fd)
    connection = database.connection = sqlite3.connect(database.path, check_same_thread=False)
    database._stack.callback(connection.close)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA synchronous=FULL')
    if not connection.execute('SELECT name FROM sqlite_master').fetchall():
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for statement in SCHEMAS[profile]:
                connection.execute(statement)
            connection.execute({'registry': 'PRAGMA application_id=0x4E594243',
                'history': 'PRAGMA application_id=0x4E594248'}[profile])
            connection.execute('PRAGMA user_version=1')
    _validate(connection, profile)
    if profile=='history' and connection.execute('PRAGMA user_version').fetchone()[0]==1:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for statement in PROJECTION_SCHEMA:connection.execute(statement)
            connection.execute('PRAGMA user_version=2')
        _validate(connection,profile)
    if profile=='history' and connection.execute('PRAGMA user_version').fetchone()[0]==2:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            for statement in USAGE_SCHEMA:connection.execute(statement)
            connection.execute('PRAGMA user_version=3')
        _validate(connection,profile)
    connection.execute('PRAGMA journal_mode=WAL')
