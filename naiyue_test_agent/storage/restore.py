"""Explicit offline whole-workspace restoration under the existing instance lock."""

from contextlib import ExitStack
import logging
import os
import sqlite3
from uuid import uuid4

from .backup import _ID, validate_backup
from .files import acquire_store_lock, release_store_lock, check_store_directory, open_store_file, open_store_readonly, sync_store_directory
from . import restore_record as records


logger = logging.getLogger(__name__)
REASONS = frozenset({'backup_invalid', 'backup_unbound', 'restore_cancelled', 'restore_conflict',
    'restore_failed', 'storage_in_use', 'workspace_id_conflict', 'workspace_previous_root_unconfirmed'})


def _selection(location, backup_id):
    manifest = validate_backup(location.database_path, backup_id)
    if manifest['workspace_id'] is None:
        raise ValueError('backup_unbound')
    if manifest['workspace_id'] != location.workspace_id:
        raise ValueError('backup_invalid')
    expected = {'source_schema': manifest['source_schema'], 'candidate_bytes': manifest['database_bytes'],
                'candidate_sha256': manifest['sha256']}
    source = location.database_path.with_name(location.database_path.name + '.backups') / backup_id / 'database.sqlite'
    records.verify_snapshot(source, expected, location)
    return manifest, expected, source


def _prepare(location, backup_id):
    manifest, expected, source = _selection(location, backup_id)
    directory, operation, marker = records.paths(location.database_path, uuid4().hex)
    directory.mkdir(mode=0o700, exist_ok=True)
    check_store_directory(directory)
    operation.mkdir(mode=0o700)
    check_store_directory(operation)
    sync_store_directory(directory)
    original = operation / 'original'
    original.mkdir(mode=0o700)
    candidate = operation / 'candidate.sqlite'
    with os.fdopen(open_store_readonly(source), 'rb') as incoming:
        with os.fdopen(open_store_file(candidate, exclusive_create=True), 'wb') as outgoing:
            while chunk := incoming.read(1024 * 1024):
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    records.verify_snapshot(candidate, expected, location)
    group = [records.file_facts(location.database_path.with_name(name)) for name in records.names(location.database_path)
             if records.exists(location.database_path.with_name(name))]
    record = {'format_version': 1, 'operation_id': operation.name, 'backup_id': backup_id,
        'workspace_id': location.workspace_id, 'database_name': location.database_path.name,
        **expected, 'phase': 'prepared', 'original_files': group}
    records.check_location(location)
    sync_store_directory(original)
    sync_store_directory(operation)
    sync_store_directory(directory.parent)
    if records.exists(marker):
        raise ValueError('restore_conflict')
    records.write_record(marker, record)
    return record


def _finish(location, record):
    database = location.database_path
    directory, operation, marker = records.paths(database, record['operation_id'])
    installed = records.inspect_files(location, record)
    if not installed:
        for item in record['original_files']:
            records.inspect_files(location, record)
            source, target = database.with_name(item['filename']), operation / 'original' / item['filename']
            if records.exists(source):
                if records.exists(target):
                    raise ValueError('restore_conflict')
                records.verify_file(source, item)
                os.rename(source, target)
                sync_store_directory(target.parent)
                sync_store_directory(database.parent)
        records.inspect_files(location, record)
        if records.exists(database):
            raise ValueError('restore_conflict')
        os.rename(operation / 'candidate.sqlite', database)
        sync_store_directory(operation)
        sync_store_directory(database.parent)
    records.inspect_files(location, record)
    record = {**record, 'phase': 'installed'}
    records.write_record(marker, record)
    # Validate again after the stage update; the stage alone is never authority.
    records.inspect_files(location, record)
    completion = operation / 'operation.json'
    if not records.exists(completion):
        records.write_record(completion, {**record, 'phase': 'completed'})
    records.inspect_files(location, record)
    if records.read_record(marker, location) != record:
        raise ValueError('restore_conflict')
    os.unlink(marker)
    sync_store_directory(directory)
    logger.info('storage_restore_completed operation_id=%s backup_id=%s source_schema=%s',
                record['operation_id'], record['backup_id'], record['source_schema'])
    return {'status': 'completed', 'operation_id': record['operation_id'], 'backup_id': record['backup_id'],
            'database_path': str(database), 'original_directory': str(operation / 'original')}


def restore_backup(location, backup_id, *, confirm):
    """confirm(preview) reads the full ID while this function retains ownership."""
    stage, primary, stack = 'lock', None, ExitStack()
    try:
        if not isinstance(backup_id, str) or not _ID.fullmatch(backup_id):
            raise ValueError('backup_invalid')
        check_store_directory(location.database_path.parent)
        fd = open_store_file(location.database_path.with_name(location.database_path.name + '.lock'))
        stack.callback(os.close, fd)
        try:
            acquire_store_lock(fd)
        except BlockingIOError as error:
            raise ValueError('storage_in_use') from error
        stack.callback(release_store_lock, fd)
        stage = 'validate'
        records.check_location(location)
        directory = location.database_path.with_name(location.database_path.name + '.maintenance')
        marker = directory / 'restore.json'
        if records.exists(directory):
            check_store_directory(directory)
        resume = records.exists(marker)
        if resume:
            try:
                record = records.read_record(marker, location)
                if record['backup_id'] != backup_id:
                    raise ValueError('restore_conflict')
                records.inspect_files(location, record)
            except (OSError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error) as error:
                raise ValueError('restore_conflict') from error
            preview = {'backup_id': backup_id, 'source_schema': record['source_schema'],
                       'phase': record['phase'], 'operation_id': record['operation_id']}
        else:
            preview, _, _ = _selection(location, backup_id)
        stage = 'confirm'
        try:
            answer = confirm({**preview, 'database_path': str(location.database_path), 'resume': resume})
        except EOFError as error:
            raise ValueError('restore_cancelled') from error
        if not isinstance(answer, str) or answer != backup_id:
            raise ValueError('restore_cancelled')
        records.check_location(location)
        stage = 'restore'
        if not resume:
            record = _prepare(location, backup_id)
        elif records.read_record(marker, location) != record:
            raise ValueError('restore_conflict')
        return _finish(location, record)
    except BaseException as error:
        primary = error
        logger.error('storage_restore_failed stage=%s error_type=%s', stage, type(error).__name__)
        if isinstance(error, (OSError, ValueError, TypeError, KeyError, RecursionError, sqlite3.Error)):
            if isinstance(error, ValueError) and str(error) in REASONS:
                raise
            raise ValueError('restore_failed') from error
        raise
    finally:
        try:
            stack.close()
        except OSError as error:
            logger.error('storage_restore_lock_cleanup_failed error_type=%s', type(error).__name__)
            if primary is None:
                raise ValueError('restore_failed') from error
