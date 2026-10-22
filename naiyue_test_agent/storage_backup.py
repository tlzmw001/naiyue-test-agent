"""Migration snapshots and offline listing, independent of application setup."""

from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID, uuid4

from . import __version__
from .storage_backup_schema import SUPPORTED_SCHEMAS, check_snapshot, read_schema_version
from .storage_files import (
    check_store_directory, check_store_file, open_store_file, open_store_readonly, sync_store_directory,
    changed_store_fields,
)


logger = logging.getLogger(__name__)
_ID = re.compile(r'[0-9a-f]{32}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_PACKAGES = ('langgraph', 'langgraph-checkpoint', 'langgraph-checkpoint-sqlite')
_FIELDS = {'format_version', 'backup_id', 'created_at', 'reason', 'creator_version',
    'source_schema', 'target_schema', 'workspace_id', 'database_name', 'checkpoint_versions',
    'database_bytes', 'sha256'}


def _root(database):
    return database.with_name(database.name + '.backups')


def _filename(value):
    return (isinstance(value, str) and 0 < len(value) <= 255 and value not in ('.', '..')
            and not any(ord(char) < 32 or char in '/\\:\x7f' for char in value))


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('backup_invalid')
        value[key] = item
    return value


def _manifest(path, backup_id, database_name):
    with os.fdopen(open_store_readonly(path), 'rb') as stream:
        body = stream.read(65537)
    if len(body) > 65536:
        raise ValueError('backup_invalid')
    value = json.loads(body, object_pairs_hook=_unique)
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError('backup_invalid')
    if (type(value['format_version']) is not int or value['format_version'] != 1
            or value['backup_id'] != backup_id or value['reason'] != 'migration'
            or not _filename(value['database_name']) or value['database_name'] != database_name):
        raise ValueError('backup_invalid')
    for key in ('source_schema', 'target_schema', 'database_bytes'):
        if type(value[key]) is not int or not 0 < value[key] <= 2**63 - 1:
            raise ValueError('backup_invalid')
    if value['source_schema'] not in SUPPORTED_SCHEMAS or value['target_schema'] < value['source_schema']:
        raise ValueError('backup_invalid')
    if (not isinstance(value['sha256'], str) or not _DIGEST.fullmatch(value['sha256'])
            or not isinstance(value['created_at'], str) or len(value['created_at']) > 64):
        raise ValueError('backup_invalid')
    stamp = datetime.fromisoformat(value['created_at'])
    if stamp.utcoffset() != timedelta(0) or 'T' not in value['created_at']:
        raise ValueError('backup_invalid')
    identity = value['workspace_id']
    if identity is not None and (not isinstance(identity, str) or str(UUID(identity)) != identity):
        raise ValueError('backup_invalid')
    packages = value['checkpoint_versions']
    if not isinstance(packages, dict) or set(packages) != set(_PACKAGES):
        raise ValueError('backup_invalid')
    for item in [value['creator_version'], *packages.values()]:
        if not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9.+!_-]{1,128}', item):
            raise ValueError('backup_invalid')
    return value


def _file_digest(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    amount = 0
    while block := stream.read(1024 * 1024):
        digest.update(block)
        amount += len(block)
    return amount, digest.hexdigest()


def _standalone(path):
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            path.with_name(path.name + suffix).lstat()
        except FileNotFoundError:
            continue
        raise ValueError('backup_invalid')


def validate_backup(database_path, backup_id, *, workspace_id=None):
    """Return format1 facts only after checking their closed SQLite snapshot."""
    stage, changed = 'location', ()
    try:
        database = Path(database_path).expanduser().absolute()
        if not isinstance(backup_id, str) or not _ID.fullmatch(backup_id):
            raise ValueError('backup_invalid')
        directory = _root(database) / backup_id
        check_store_directory(directory)
        stage = 'manifest'
        manifest = _manifest(directory / 'manifest.json', backup_id, database.name)
        stage = 'workspace'
        if workspace_id is not None and manifest['workspace_id'] != workspace_id:
            raise ValueError('backup_invalid')
        snapshot = directory / 'database.sqlite'
        stage = 'sidecar'
        _standalone(snapshot)
        stage = 'digest'
        with os.fdopen(open_store_readonly(snapshot), 'rb') as stream:
            before = os.fstat(stream.fileno())
            named_before = snapshot.lstat()
            if _file_digest(stream) != (manifest['database_bytes'], manifest['sha256']):
                raise ValueError('backup_invalid')
            stage = 'snapshot'
            facts = check_snapshot(snapshot)
            stage = 'facts'
            if any(facts[key] != manifest[key] for key in ('source_schema', 'workspace_id')):
                raise ValueError('backup_invalid')
            stage = 'identity'
            descriptor_after = os.fstat(stream.fileno())
            after = snapshot.lstat()
            changed = changed_store_fields(before, named_before, descriptor_after, after)
            if changed:
                raise ValueError('backup_invalid')
        stage = 'sidecar'
        _standalone(snapshot)
        return manifest
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, sqlite3.Error) as error:
        logger.warning('backup_validation_failed stage=%s changed_fields=%s error_type=%s',
                       stage, ','.join(changed) or 'none', type(error).__name__)
        raise ValueError('backup_invalid') from error


def create_backup(connection, database_path, *, target_schema):
    """Caller owns the instance lock and supplies its idle, pre-migration connection."""
    stage = 'source'
    try:
        database = Path(database_path).expanduser().absolute()
        if connection.in_transaction or not _filename(database.name) or type(target_schema) is not int:
            raise ValueError('backup_invalid')
        check_store_directory(database.parent)
        check_store_file(database)
        source = next((row[2] for row in connection.execute('PRAGMA database_list') if row[1] == 'main'), '')
        if not source or not Path(source).samefile(database):
            raise ValueError('backup_invalid')
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                check_store_file(database.with_name(database.name + suffix))
            except FileNotFoundError:
                continue
        source_schema = read_schema_version(connection)
        if source_schema not in SUPPORTED_SCHEMAS or not source_schema <= target_schema <= 2**63 - 1:
            raise ValueError('unsupported_store_schema')
        backup_id = uuid4().hex
        root = _root(database)
        root.mkdir(mode=0o700, exist_ok=True)
        check_store_directory(root)
        pending = root / ('.pending-' + backup_id)
        pending.mkdir(mode=0o700)
        snapshot = pending / 'database.sqlite'
        stage = 'snapshot'
        with os.fdopen(open_store_file(snapshot, exclusive_create=True), 'r+b') as stream:
            destination = sqlite3.connect(snapshot)
            primary = None
            try:
                connection.backup(destination, pages=256)
                destination.execute('PRAGMA journal_mode=DELETE')
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    destination.close()
                except BaseException as error:
                    logger.error('backup_connection_cleanup_failed error_type=%s', type(error).__name__)
                    if primary is None:
                        raise
            _standalone(snapshot)
            facts = check_snapshot(snapshot)
            if facts['source_schema'] != source_schema:
                raise ValueError('backup_invalid')
            amount, digest = _file_digest(stream)
            os.fsync(stream.fileno())
        manifest = {'format_version': 1, 'backup_id': backup_id,
            'created_at': datetime.now(timezone.utc).isoformat(), 'reason': 'migration',
            'creator_version': __version__, **facts, 'target_schema': target_schema,
            'database_name': database.name, 'checkpoint_versions': {name: version(name) for name in _PACKAGES},
            'database_bytes': amount, 'sha256': digest}
        stage = 'manifest'
        with os.fdopen(open_store_file(pending / 'manifest.json', exclusive_create=True), 'w', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        # Validate the serialized manifest as well as the SQLite data before publication.
        _manifest(pending / 'manifest.json', backup_id, database.name)
        sync_store_directory(pending)
        stage = 'publish'
        final = root / backup_id
        if final.exists() or final.is_symlink():
            raise FileExistsError('backup_collision')
        os.rename(pending, final)
        sync_store_directory(root)
        logger.info('backup_created backup_id=%s source_schema=%s target_schema=%s', backup_id, facts['source_schema'], target_schema)
        return manifest
    except BaseException as error:
        logger.error('backup_create_failed stage=%s error_type=%s', stage, type(error).__name__)
        if not isinstance(error, Exception):
            raise
        raise ValueError('backup_failed') from error


def list_backups(database_path, *, workspace_id=None):
    """List invalid/incomplete evidence as well as validated backups; never write."""
    try:
        database = Path(database_path).expanduser().absolute()
        root = _root(database)
        try:
            check_store_directory(root)
        except FileNotFoundError:
            return []
        rows = []
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            name = entry.name
            safe_name = name if _ID.fullmatch(name) else json.dumps(name[:128], ensure_ascii=True)[1:-1]
            if name.startswith('.pending-') and _ID.fullmatch(name[9:]):
                try:
                    check_store_directory(entry)
                except OSError as error:
                    logger.warning('backup_entry_invalid error_type=%s', type(error).__name__)
                    rows.append({'backup_id': safe_name, 'status': 'invalid', 'error': 'backup_invalid'})
                else:
                    logger.warning('backup_entry_incomplete')
                    rows.append({'backup_id': safe_name, 'status': 'incomplete'})
            elif not _ID.fullmatch(name):
                logger.warning('backup_entry_invalid reason=invalid_name')
                rows.append({'backup_id': safe_name, 'status': 'invalid', 'error': 'backup_invalid'})
            else:
                try:
                    manifest = validate_backup(database, name, workspace_id=workspace_id)
                except ValueError:
                    rows.append({'backup_id': name, 'status': 'invalid', 'error': 'backup_invalid'})
                else:
                    rows.append({'backup_id': name, 'status': 'valid', 'manifest': manifest})
        rows.sort(key=lambda row: row['backup_id'])
        rows.sort(key=lambda row: datetime.fromisoformat(row['manifest']['created_at'])
                  if row['status'] == 'valid' else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return rows
    except (OSError, ValueError) as error:
        logger.error('backup_list_failed error_type=%s', type(error).__name__)
        raise ValueError('backup_invalid') from error
