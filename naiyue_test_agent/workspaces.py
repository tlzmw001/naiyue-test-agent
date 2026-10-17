"""Stable local workspace identities; SQLite owns the authoritative root binding."""

from contextlib import ExitStack, closing
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from uuid import UUID, uuid4

from .storage_files import acquire_store_lock, check_store_file, open_store_file, release_store_lock


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkspaceLocation:
    workspace_id: str
    root: Path
    database_path: Path


def _read_marker(root):
    marker = root / '.naiyue' / 'workspace.json'
    try:
        check_store_file(marker)
    except FileNotFoundError:
        return None
    if marker.stat().st_size > 4096:
        raise ValueError('invalid_workspace_manifest')
    try:
        raw = json.loads(marker.read_text(encoding='utf-8'))
        identity = raw['workspace_id']
        if (set(raw) != {'version', 'workspace_id'} or type(raw['version']) is not int
                or raw['version'] != 1 or str(UUID(identity)) != identity):
            raise ValueError('invalid_workspace_manifest')
        return identity
    except (ValueError, KeyError, TypeError, UnicodeError, AttributeError) as error:
        raise ValueError('invalid_workspace_manifest') from error


def _write_marker(root, identity):
    marker = root / '.naiyue' / 'workspace.json'
    fd, temporary = tempfile.mkstemp(dir=marker.parent, prefix='.workspace-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'version': 1, 'workspace_id': identity}, stream)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass  # Successful replacement already consumed the temporary file.


def check_workspace_relocation(identity, old_root, root):
    """Missing old location permits a move; a live second copy never takes over."""
    previous = Path(old_root)
    try:
        previous.stat()
    except FileNotFoundError:
        return
    if previous.samefile(root):
        return
    old_identity = _read_marker(previous)
    if old_identity == identity:
        raise ValueError('workspace_id_conflict')
    if old_identity is None:
        raise ValueError('workspace_previous_root_unconfirmed')


def _read_binding(database):
    try:
        check_store_file(database)
    except (FileNotFoundError, NotADirectoryError):
        return None
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            check_store_file(database.with_name(database.name + suffix))
        except FileNotFoundError:
            pass
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute('SELECT workspace_id,root_path FROM naiyue_workspace').fetchall()
        if len(rows) > 1:
            raise ValueError('invalid_workspace_binding')
        return dict(rows[0]) if rows else None


def prepare_workspace(root, *, data_dir, database_path=None, new_workspace=False, create=True):
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('invalid_workspace_root')
    data_dir = Path(data_dir).expanduser().absolute()
    if new_workspace and (database_path is not None or not create):
        raise ValueError('new_workspace_conflicts')
    directory = root / '.naiyue'
    try:
        identity = _read_marker(root)
        if not create and identity is None:
            if database_path is not None:
                try:
                    check_store_file(Path(database_path).expanduser().absolute())
                except FileNotFoundError:
                    return None
                raise ValueError('workspace_manifest_missing')
            return None
        if create:
            directory.mkdir(exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('invalid_workspace_directory')
        stack = ExitStack()
        primary = None
        try:
            if create:
                fd = open_store_file(directory / 'workspace.lock')
                stack.callback(os.close, fd)
                acquire_store_lock(fd, blocking=True)
                stack.callback(release_store_lock, fd)
            identity = _read_marker(root)
            if identity is None:
                if new_workspace:
                    raise ValueError('not_a_workspace_copy')
                identity = str(uuid4())
                _write_marker(root, identity)
            database = (Path(database_path).expanduser().absolute() if database_path is not None
                        else data_dir / 'workspaces' / identity / 'sessions.sqlite')
            binding = _read_binding(database)
            conflict = False
            if binding:
                if binding['workspace_id'] != identity:
                    raise ValueError('workspace_database_mismatch')
                try:
                    check_workspace_relocation(identity, binding['root_path'], root)
                except ValueError as error:
                    if str(error) != 'workspace_id_conflict':
                        raise
                    conflict = True
            if new_workspace:
                if not conflict:
                    raise ValueError('not_a_workspace_copy')
                identity = str(uuid4())
                database = data_dir / 'workspaces' / identity / 'sessions.sqlite'
                _write_marker(root, identity)
            elif conflict:
                raise ValueError('workspace_id_conflict')
            return WorkspaceLocation(identity, root, database)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                stack.close()
            except OSError as error:
                logger.error('workspace_lock_cleanup_failed error_type=%s', type(error).__name__)
                if primary is None:
                    raise

    except (OSError, ValueError, sqlite3.Error) as error:
        logger.error('workspace_prepare_failed error_type=%s', type(error).__name__)
        raise
