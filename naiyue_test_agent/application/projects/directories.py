"""Read local directory metadata without registering or trusting a project."""
import logging
import os
from pathlib import Path
import stat

from naiyue_test_agent.contracts.projects import ProjectError
from .locations import availability, canonical, directory_facts, directory_identity, require_same_directory

logger = logging.getLogger(__name__)


def browse_directories(path=None, *, default_parent=None, show_hidden=False, offset=0, limit=100):
    home = Path.home()
    default_error = None
    if default_parent is not None:
        status = availability(default_parent)
        if status != 'available':
            default_error = 'project_directory_' + status
    start = default_parent if default_parent is not None and default_error is None else home
    root = canonical(path if path is not None else start)
    facts = directory_facts(root)
    entries = []
    try:
        with os.scandir(root) as source:
            for entry in source:
                if not entry.is_dir(follow_symlinks=True):
                    continue
                info = entry.stat(follow_symlinks=False)
                hidden = entry.name.startswith('.') or bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_HIDDEN)
                if hidden and not show_hidden:
                    continue
                entries.append({'name': entry.name, 'path': str(root / entry.name), 'symlink': entry.is_symlink()})
    except OSError as error:
        logger.warning('project_directory_list_failed error_type=%s', type(error).__name__)
        code = 'missing' if isinstance(error, FileNotFoundError) else 'not_directory' if isinstance(error, NotADirectoryError) else 'unreadable'
        raise ProjectError('project_directory_' + code) from error
    require_same_directory(root, facts)
    entries.sort(key=lambda item: (item['name'].casefold(), item['name']))
    candidates = [('默认位置', default_parent), ('主文件夹', home), ('桌面', home / 'Desktop'), ('文稿', home / 'Documents')]
    drives = os.listdrives() if os.name == 'nt' else [home.anchor]
    candidates.extend(('磁盘 ' + drive, Path(drive)) for drive in drives)
    positions, seen = [], set()
    for name, candidate in candidates:
        if candidate is not None and str(candidate) not in seen and availability(candidate) == 'available':
            positions.append({'name': name, 'path': str(candidate)})
            seen.add(str(candidate))
    end = offset + limit
    return {'path': str(root), 'parent': str(root.parent) if root.parent != root else None,
            'separator': os.sep, 'identity': directory_identity(root, facts),
            'items': entries[offset:end], 'next_offset': end if end < len(entries) else None,
            'positions': positions, 'default_parent_dir': str(default_parent) if default_parent is not None else None,
            'default_error': default_error}
