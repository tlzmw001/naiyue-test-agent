"""Strict restore evidence and closed-file verification; no application runtime."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID, uuid4

from .backup import _ID, _DIGEST, _filename, _unique, _file_digest, _standalone
from .backup_schema import SUPPORTED_SCHEMAS, check_snapshot
from .files import check_store_directory, open_store_file, open_store_readonly, sync_store_directory, changed_store_fields
from naiyue_test_agent.workspaces import check_workspace_relocation, locate_workspace


FIELDS = {'format_version', 'operation_id', 'backup_id', 'workspace_id', 'database_name',
          'source_schema', 'candidate_bytes', 'candidate_sha256', 'phase', 'original_files'}


def paths(database, operation_id):
    directory = database.with_name(database.name + '.maintenance')
    return directory, directory / operation_id, directory / 'restore.json'


def names(database):
    return tuple(database.name + suffix for suffix in ('', '-wal', '-shm', '-journal'))


def exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _integer(value, *, positive=False):
    return type(value) is int and int(positive) <= value <= 2**63 - 1


def read_record(path, location, *, completed=False):
    check_store_directory(path.parent)
    with os.fdopen(open_store_readonly(path), 'rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('restore_conflict')
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('restore_conflict')
    if (type(value['format_version']) is not int or value['format_version'] != 1
            or not isinstance(value['operation_id'], str) or not _ID.fullmatch(value['operation_id'])
            or not isinstance(value['backup_id'], str) or not _ID.fullmatch(value['backup_id'])
            or value['workspace_id'] != location.workspace_id
            or str(UUID(value['workspace_id'])) != value['workspace_id']
            or not _filename(value['database_name']) or value['database_name'] != location.database_path.name
            or type(value['source_schema']) is not int or value['source_schema'] not in SUPPORTED_SCHEMAS
            or not _integer(value['candidate_bytes'], positive=True)
            or not isinstance(value['candidate_sha256'], str) or not _DIGEST.fullmatch(value['candidate_sha256'])
            or value['phase'] not in (('completed',) if completed else ('prepared', 'installed'))
            or not isinstance(value['original_files'], list) or len(value['original_files']) > 4):
        raise ValueError('restore_conflict')
    seen = set()
    for item in value['original_files']:
        if (not isinstance(item, dict) or set(item) != {'filename', 'bytes', 'sha256'}
                or not isinstance(item['filename'], str) or item['filename'] not in names(location.database_path)
                or item['filename'] in seen or not _integer(item['bytes'])
                or not isinstance(item['sha256'], str) or not _DIGEST.fullmatch(item['sha256'])):
            raise ValueError('restore_conflict')
        seen.add(item['filename'])
    return value


def write_record(path, value):
    check_store_directory(path.parent)
    temporary = path.with_name('.record-' + uuid4().hex + '.tmp')
    with os.fdopen(open_store_file(temporary, exclusive_create=True), 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_store_directory(path.parent)


def file_facts(path):
    check_store_directory(path.parent)
    with os.fdopen(open_store_readonly(path), 'rb') as stream:
        before = os.fstat(stream.fileno())
        named_before = path.lstat()
        amount, digest = _file_digest(stream)
        if changed_store_fields(before, named_before, os.fstat(stream.fileno()), path.lstat()):
            raise ValueError('restore_conflict')
    return {'filename': path.name, 'bytes': amount, 'sha256': digest}


def verify_file(path, expected):
    actual = file_facts(path)
    if any(actual[key] != expected[key] for key in ('bytes', 'sha256')):
        raise ValueError('restore_conflict')


def check_location(location):
    current = locate_workspace(location.root, data_dir=None, database_path=location.database_path)
    if current != location:
        raise ValueError('restore_conflict')


def verify_snapshot(path, expected, location):
    """Hold a read handle across path-based SQLite checks, then close before rename."""
    check_location(location)
    check_store_directory(path.parent)
    _standalone(path)
    with os.fdopen(open_store_readonly(path), 'rb') as stream:
        before = os.fstat(stream.fileno())
        named_before = path.lstat()
        if _file_digest(stream) != (expected['candidate_bytes'], expected['candidate_sha256']):
            raise ValueError('restore_conflict')
        if check_snapshot(path) != {'source_schema': expected['source_schema'], 'workspace_id': location.workspace_id}:
            raise ValueError('restore_conflict')
        with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
            connection.execute('PRAGMA trusted_schema=OFF')
            old_root = connection.execute('SELECT root_path FROM naiyue_workspace').fetchone()[0]
        check_workspace_relocation(location.workspace_id, old_root, location.root)
        if changed_store_fields(before, named_before, os.fstat(stream.fileno()), path.lstat()):
            raise ValueError('restore_conflict')
    _standalone(path)


def inspect_files(location, record):
    """Return whether installation is proven; reject every ambiguous file group."""
    database = location.database_path
    directory, operation, _ = paths(database, record['operation_id'])
    check_store_directory(directory)
    check_store_directory(operation)
    original, candidate = operation / 'original', operation / 'candidate.sqlite'
    check_store_directory(original)
    expected = {item['filename']: item for item in record['original_files']}
    if any(path.name not in expected for path in original.iterdir()):
        raise ValueError('restore_conflict')
    preserved = all(exists(original / name) for name in expected)
    installed = preserved and not exists(candidate) and exists(database)
    for name in names(database):
        active, archived = database.with_name(name), original / name
        if name in expected:
            if installed and name == database.name:
                verify_file(archived, expected[name])
            elif exists(active) == exists(archived):
                raise ValueError('restore_conflict')
            else:
                verify_file(archived if exists(archived) else active, expected[name])
        elif exists(active) and not (installed and name == database.name):
            raise ValueError('restore_conflict')
        if installed and name != database.name and exists(active):
            raise ValueError('restore_conflict')
    if record['phase'] == 'installed' and not installed:
        raise ValueError('restore_conflict')
    verify_snapshot(database if installed else candidate, record, location)
    completion = operation / 'operation.json'
    if exists(completion):
        completed = read_record(completion, location, completed=True)
        if not installed or completed != {**record, 'phase': 'completed'}:
            raise ValueError('restore_conflict')
    return installed
