"""Read legacy identities without migration; initialize only reserved new stores."""
from contextlib import ExitStack, contextmanager
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from naiyue_test_agent.contracts.projects import ProjectError,GitSource
from .backup_schema import CURRENT_SCHEMA, read_schema_version, SUPPORTED_SCHEMAS
from .files import acquire_store_lock, check_store_directory, check_store_file, open_store_file, release_store_lock
from .maintenance import check_restore_marker
from .project_store import ProjectStore
from naiyue_test_agent.config_paths import DEFAULT_BUSY_TIMEOUT_MS

logger = logging.getLogger(__name__)


@contextmanager
def locked_project_reader(path):
    """The lock remains owned through the caller's dependent registry operation."""
    path = Path(path)
    with ExitStack() as stack:
        check_store_directory(path.parent)
        check_store_file(path)
        fd = open_store_file(path.with_name(path.name + '.lock'))
        stack.callback(os.close, fd)
        try:
            acquire_store_lock(fd)
        except BlockingIOError as error:
            raise ProjectError('project_in_use') from error
        stack.callback(release_store_lock, fd)
        check_restore_marker(path)
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                check_store_file(path.with_name(path.name + suffix))
            except FileNotFoundError:
                pass
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        stack.callback(connection.close)
        connection.execute('PRAGMA trusted_schema=OFF')
        yield connection


def _legacy_facts(connection):
    if read_schema_version(connection) not in SUPPORTED_SCHEMAS:
        raise ProjectError('project_database_invalid')
    rows = connection.execute('SELECT workspace_id,root_path FROM naiyue_workspace').fetchall()
    if not rows:
        raise ProjectError('project_unbound_database')
    if len(rows) != 1:
        raise ProjectError('project_database_invalid')
    identity, raw = rows[0]
    try:
        root = Path(raw)
        if (str(UUID(identity)) != identity or not root.is_absolute() or '..' in root.parts
                or '\0' in raw or str(root) != raw):
            raise ValueError('invalid_binding')
    except (ValueError, TypeError, AttributeError) as error:
        raise ProjectError('project_database_invalid') from error
    fields = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_sessions)')}
    if 'project_id' in fields and connection.execute(
            'SELECT 1 FROM naiyue_sessions WHERE project_id IS NOT ? LIMIT 1', (identity,)).fetchone():
        raise ProjectError('project_identity_conflict')
    return identity, root


@contextmanager
def legacy_guard(path):
    with ExitStack() as stack:
        try:
            facts = _legacy_facts(stack.enter_context(locked_project_reader(path)))
        except ProjectError:
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            logger.error('project_database_read_failed error_type=%s', type(error).__name__)
            raise ProjectError('project_database_invalid') from error
        yield facts


def read_legacy(path):
    with legacy_guard(path) as facts:
        return facts


def validate_runtime_connection(connection, profile):
    """Under the store lock, before migration or checkpoint deserialization."""
    if profile != 'chats':
        identities = (connection.execute('SELECT project_id,source_kind FROM naiyue_project_identity').fetchall()
            if read_schema_version(connection)>=20 else [])
        if identities and len(identities)==1 and identities[0][1]=='git':
            identity=identities[0][0]
            if connection.execute('SELECT 1 FROM naiyue_workspace LIMIT 1').fetchone():
                raise ProjectError('project_identity_conflict')
        else:
            identity, _ = _legacy_facts(connection)
        if read_schema_version(connection)>=20 and [tuple(row) for row in identities] != [(identity,'git' if identities and identities[0][1]=='git' else 'local')]:
            raise ProjectError('project_identity_conflict')
        if identity != profile:
            raise ProjectError('project_identity_conflict')
        fields = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_sessions)')}
        if 'project_id' in fields and connection.execute('SELECT 1 FROM naiyue_sessions WHERE project_id IS NOT ? LIMIT 1',(identity,)).fetchone():
            raise ProjectError('project_identity_conflict')
        child_fields = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_subagent_instances)')}
        if 'project_id' in child_fields and connection.execute(
                'SELECT 1 FROM naiyue_subagent_instances WHERE project_id IS NOT ? LIMIT 1', (identity,)).fetchone():
            raise ProjectError('project_identity_conflict')
        return
    if read_schema_version(connection) not in SUPPORTED_SCHEMAS:
        raise ProjectError('project_database_invalid')
    if connection.execute('SELECT 1 FROM naiyue_workspace LIMIT 1').fetchone():
        raise ProjectError('chat_database_mismatch')
    if read_schema_version(connection)>=20 and connection.execute('SELECT 1 FROM naiyue_project_identity LIMIT 1').fetchone():
        raise ProjectError('chat_database_mismatch')
    fields = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_sessions)')}
    if connection.execute('SELECT 1 FROM naiyue_sessions WHERE workspace_root IS NOT NULL LIMIT 1').fetchone():
        raise ProjectError('chat_database_mismatch')
    if 'project_id' in fields and connection.execute(
            'SELECT 1 FROM naiyue_sessions WHERE project_id IS NOT NULL OR execution_root_kind IS NOT NULL LIMIT 1').fetchone():
        raise ProjectError('chat_database_mismatch')
    child_fields = {row[1] for row in connection.execute('PRAGMA table_info(naiyue_subagent_instances)')}
    if 'project_id' in child_fields and connection.execute('''SELECT 1 FROM naiyue_subagent_instances
            WHERE project_id IS NOT NULL OR execution_root IS NOT NULL OR execution_root_kind IS NOT NULL LIMIT 1''').fetchone():
        raise ProjectError('chat_database_mismatch')


def _file_receipt(path):
    info = path.stat()
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'device': info.st_dev, 'inode': info.st_ino, 'sha256': digest}


def validate_reserved_connection(connection, path, project, expected_file):
    binding = connection.execute('SELECT workspace_id,root_path FROM naiyue_workspace').fetchall()
    if binding:
        if isinstance(project.source,GitSource) or len(binding) != 1 or tuple(binding[0]) != (project.project_id, str(project.source.path)):
            raise ProjectError('project_identity_conflict')
    else:
        identities=connection.execute('SELECT project_id,source_kind FROM naiyue_project_identity').fetchall()
        if identities:
            if not isinstance(project.source,GitSource) or [tuple(row) for row in identities]!=[(project.project_id,'git')]:
                raise ProjectError('project_identity_conflict')
        elif expected_file is None or _file_receipt(path) != expected_file:
            raise ProjectError('project_database_unconfirmed')
    if read_schema_version(connection) != CURRENT_SCHEMA:
        raise ProjectError('project_identity_conflict')


@contextmanager
def initialize_reserved_store(project, *, expected_file=None, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
    path = project.database_path
    with ProjectStore.open_reserved(project, expected_file, busy_timeout_ms=busy_timeout_ms) as store:
        yield store, _file_receipt(path)


def bind_reserved_identity(store, project):
    if isinstance(project.source,GitSource):
        store.sessions.bind_git_project(project.project_id)
        return
    binding = store.sessions.get_workspace_binding()
    if binding is None:
        store.sessions.bind_workspace(project.project_id, project.source.path)
    elif binding != {'workspace_id': project.project_id, 'root_path': str(project.source.path)}:
        raise ProjectError('project_identity_conflict')


@contextmanager
def deletion_guard(project):
    with locked_project_reader(project.database_path) as connection:
        validate_runtime_connection(connection,project.project_id)
        if connection.execute("SELECT 1 FROM naiyue_sessions WHERE state IN ('running','interrupted','review_required') LIMIT 1").fetchone():
            raise ProjectError('project_in_use')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='naiyue_subagent_instances'").fetchone():
            if connection.execute("SELECT 1 FROM naiyue_subagent_instances WHERE state IN ('running','interrupted','pending_parent') LIMIT 1").fetchone():
                raise ProjectError('project_in_use')
        yield
