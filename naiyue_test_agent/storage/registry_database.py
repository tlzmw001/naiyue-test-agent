"""A separate SQLite format, using the existing Database owner and transactions."""
import os
import sqlite3

from .files import (acquire_store_lock, check_store_directory, check_store_file,
                    open_store_file, release_store_lock)
from naiyue_test_agent.contracts.projects import ProjectError


APPLICATION_ID = 0x4E595450


def initialize_registry(database):
    database.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_store_directory(database.path.parent)
    fd = open_store_file(database.path.with_name(database.path.name + '.lock'))
    database._stack.callback(os.close, fd)
    try:
        acquire_store_lock(fd)
    except BlockingIOError as error:
        raise ProjectError('project_registry_in_use') from error
    database._stack.callback(release_store_lock, fd)
    fd = open_store_file(database.path)
    os.close(fd)
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            check_store_file(database.path.with_name(database.path.name + suffix))
        except FileNotFoundError:
            pass
    connection = database.connection = sqlite3.connect(database.path, check_same_thread=False)
    database._stack.callback(connection.close)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA synchronous=FULL')
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        objects = connection.execute('SELECT name FROM sqlite_master').fetchall()
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        identity = connection.execute('PRAGMA application_id').fetchone()[0]
        if not objects and version == 0 and identity == 0:
            connection.execute('''CREATE TABLE projects(
                project_id TEXT PRIMARY KEY, name TEXT NOT NULL, source_kind TEXT NOT NULL,
                root TEXT UNIQUE, path_key TEXT UNIQUE, git_url TEXT, git_ref TEXT,
                database_path TEXT NOT NULL UNIQUE, lifecycle TEXT NOT NULL, trusted INTEGER NOT NULL)''')
            connection.execute('''CREATE TABLE project_operations(
                operation_id TEXT PRIMARY KEY, action TEXT NOT NULL, request_json TEXT NOT NULL,
                project_id TEXT NOT NULL, stage TEXT NOT NULL, facts_json TEXT NOT NULL,
                error_code TEXT, result_json TEXT)''')
            connection.execute('PRAGMA user_version=1')
            connection.execute(f'PRAGMA application_id={APPLICATION_ID}')
            version, identity = 1, APPLICATION_ID
        if version not in (1, 2, 3, 4, 5, 6) or identity != APPLICATION_ID:
            raise ProjectError('project_registry_invalid_format')
        if version == 1:
            connection.execute('ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
            connection.execute('PRAGMA user_version=2')
            version = 2
        if version == 2:
            connection.execute('''CREATE TABLE execution_environments(
                binding_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE, lifecycle TEXT NOT NULL, record_json TEXT NOT NULL)''')
            connection.execute('PRAGMA user_version=3')
            version=3
        if version == 3:
            connection.execute('''CREATE TABLE sandbox_operations(
                operation_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,project_id TEXT NOT NULL,
                session_id TEXT NOT NULL,record_json TEXT NOT NULL)''')
            connection.execute('PRAGMA user_version=4')
            version=4
        if version == 4:
            connection.execute('''CREATE TABLE git_inputs(
                input_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, record_json TEXT NOT NULL)''')
            connection.execute('PRAGMA user_version=5')
            version=5
        if version == 5:
            connection.execute('''CREATE TABLE git_deliveries(
                operation_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,binding_id TEXT NOT NULL,
                result_id TEXT NOT NULL,state TEXT NOT NULL,record_json TEXT NOT NULL)''')
            connection.execute('PRAGMA user_version=6')
        for table, expected in (('projects', {'project_id', 'name', 'root', 'path_key', 'source_kind',
                'git_url', 'git_ref', 'database_path', 'lifecycle', 'trusted', 'revision'}),
                ('project_operations', {'operation_id', 'action', 'request_json', 'project_id',
                 'stage', 'facts_json', 'error_code', 'result_json'}),
                ('execution_environments', {'binding_id','project_id','session_id','lifecycle','record_json'}),
                ('sandbox_operations', {'operation_id','binding_id','project_id','session_id','record_json'}),
                ('git_inputs', {'input_id','project_id','record_json'}),
                ('git_deliveries', {'operation_id','project_id','binding_id','result_id','state','record_json'})):
            # Identifiers are the fixed internal table names above.
            if {row[1] for row in connection.execute(f'PRAGMA table_info({table})')} != expected:
                raise ProjectError('project_registry_invalid_format')
