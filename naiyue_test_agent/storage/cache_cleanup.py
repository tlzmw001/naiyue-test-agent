"""Bounded read-only views of the registered cache owners."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .backup_schema import SUPPORTED_SCHEMAS, read_schema_version
from .cache_cleanup_files import CleanupIssue
from .project_databases import validate_runtime_connection
from .registry_database import APPLICATION_ID


@contextmanager
def read_connection(path):
    path = Path(path)
    from .files import check_store_directory, check_store_file
    check_store_directory(path.parent)
    check_store_file(path)
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            check_store_file(path.with_name(path.name + suffix))
        except FileNotFoundError:
            continue
    uri = path.as_uri()
    if uri.startswith('file://') and not uri.startswith('file:///'):
        uri = 'file:////' + uri.removeprefix('file://')
    connection = sqlite3.connect(uri + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA trusted_schema=OFF')
        yield connection
    finally:
        connection.close()


def registrations(connection, limit, project_id=None):
    if (connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
            or connection.execute('PRAGMA user_version').fetchone()[0] not in range(2, 7)):
        raise CleanupIssue('unsupported_schema', '项目登记库格式未知', skipped=False)
    sql = 'SELECT project_id, database_path, lifecycle, revision FROM projects'
    if project_id is not None:
        return [tuple(row) for row in connection.execute(sql + ' WHERE project_id=?', (project_id,))]
    return [tuple(row) for row in connection.execute(sql + ' ORDER BY project_id LIMIT ?', (limit,))]


def validate_cleanup_database(connection, profile):
    version = read_schema_version(connection)
    if version not in SUPPORTED_SCHEMAS:
        raise CleanupIssue('unsupported_schema', '会话数据库格式未知')
    validate_runtime_connection(connection, profile)
    return version


def session_rows(connection, version, limit, *, session_id=None):
    children = ("EXISTS (SELECT 1 FROM naiyue_subagent_instances AS child "
                "WHERE child.session_id = parent.session_id AND (child.state IS NULL OR child.state NOT IN ('ready','stopped','failed','retired')))"
                if version >= 14 else "0")
    sql = f'SELECT session_id, state, {children} FROM naiyue_sessions AS parent'
    if session_id is None:
        rows = connection.execute(sql + ' ORDER BY session_id LIMIT ?', (limit,))
    else:
        rows = connection.execute(sql + ' WHERE session_id=?', (session_id,))
    return [tuple(row) for row in rows]
