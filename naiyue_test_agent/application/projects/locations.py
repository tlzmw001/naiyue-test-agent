"""Canonical location facts; these do not grant tool execution permission."""
import logging
import hashlib
import json
import os
from pathlib import Path
import stat

from naiyue_test_agent.contracts.projects import ProjectError

logger = logging.getLogger(__name__)


def canonical(path):
    try:
        if not isinstance(path, (str, Path)) or not str(path).strip() or '\0' in str(path):
            raise ValueError('invalid_path')
        value = Path(path).expanduser()
        if not value.is_absolute():
            raise ValueError('relative_path')
        return value.resolve()
    except (OSError, ValueError, RuntimeError) as error:
        logger.warning('project_location_invalid error_type=%s', type(error).__name__)
        raise ProjectError('project_invalid_path') from error


def availability(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return 'missing'
    except NotADirectoryError:
        return 'not_directory'
    except OSError as error:
        logger.warning('project_location_check_failed error_type=%s', type(error).__name__)
        return 'unreadable'
    return 'available' if stat.S_ISDIR(info.st_mode) else 'not_directory'


def directory_facts(path):
    status = availability(path)
    if status != 'available':
        raise ProjectError('project_directory_' + status)
    if os.name == 'nt':
        from .windows_directory import directory_handle, signature
        with directory_handle(path) as handle:
            return signature(handle)
    try:
        info = path.stat()
    except OSError as error:
        logger.warning('project_location_stat_failed error_type=%s', type(error).__name__)
        raise ProjectError('project_directory_unreadable') from error
    return {'device': info.st_dev, 'inode': info.st_ino}


def directory_identity(path, facts):
    """An observation identifier, not an execution permission or bearer token."""
    return hashlib.sha256(json.dumps([str(path), facts], sort_keys=True).encode()).hexdigest()


def mkdir_under(parent, name, expected):
    if os.name == 'nt':
        from .windows_directory import directory_handle, mkdir_relative, signature
        with directory_handle(parent) as handle:
            if signature(handle) != expected:
                raise ProjectError('project_location_changed')
            return mkdir_relative(handle, name)
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if {'device': info.st_dev, 'inode': info.st_ino} != expected:
            raise ProjectError('project_location_changed')
        os.mkdir(name, dir_fd=fd)
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise ProjectError('project_location_changed')
        return {'device': info.st_dev, 'inode': info.st_ino}
    finally:
        os.close(fd)


def check_legacy_marker(root, project_id):
    from naiyue_test_agent.workspaces import _read_marker
    if availability(root) != 'available':
        return  # An unavailable old location does not prevent importing its history.
    try:
        identity = _read_marker(root)
    except (OSError, ValueError) as error:
        logger.warning('project_marker_check_failed error_type=%s', type(error).__name__)
        raise ProjectError('project_identity_unavailable') from error
    if identity is not None and identity != project_id:
        raise ProjectError('project_identity_conflict')


def require_same_directory(path, facts):
    if canonical(path) != path or directory_facts(path) != facts:
        raise ProjectError('project_location_changed')


def folder_name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 255 or value in ('.', '..')
            or any(c in value for c in ('/', '\\', '\0', ':'))
            or value.rstrip(' .') != value or os.path.isabs(value)):
        raise ProjectError('project_invalid_name')
    return value


def display_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or '\0' in value:
        raise ProjectError('project_invalid_name')
    return value.strip()
