"""Recognize capture work trees without walking unknown directories."""
from contextlib import ExitStack
from pathlib import Path
import re

from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue, cleanup_step, identity, pinned_directory, regular

STAGING_NAME = re.compile(r'capture-[a-z0-9_]{8}')
NESTED_NAME = re.compile(r'\.nyt-capture-[a-z0-9_]{8}')
FROZEN_FILE = re.compile(r'(0|[1-9][0-9]{0,8})')
BUNDLE = {'archive', 'input.json', 'manifest.json', 'selection.json', 'receipt.json', 'source.json'}


def allowed(relative, directory):
    parts = relative.split('/')
    first = parts[0]
    if len(parts) == 1:
        return (first == 'input' or bool(NESTED_NAME.fullmatch(first))) if directory else first == 'import.json'
    if first == 'input':
        return len(parts) == 2 and not directory and parts[1] in BUNDLE
    if not NESTED_NAME.fullmatch(first):
        return False
    if len(parts) == 2:
        return directory and parts[1] in ('frozen', 'result')
    return (len(parts) == 3 and not directory and
            (parts[1] == 'result' and parts[2] in BUNDLE or
             parts[1] == 'frozen' and bool(FROZEN_FILE.fullmatch(parts[2]))))


def staging_snapshot(root, run, *, limit=None):
    files, directories = {}, {}

    def collect(parent, prefix=''):
        with parent.entries() as listing:
            for entry in listing:
                if limit is None:
                    run.check(path=parent.path, unit=entry.name)
                elif len(files) + len(directories) >= limit:
                    raise CleanupIssue('unexpected_entry', '退役暂存出现新增条目')
                name = entry.name
                relative = prefix + name
                info = parent.info(name)
                if regular(info) and allowed(relative, False):
                    files[relative] = identity(info)
                elif allowed(relative, True):
                    with pinned_directory(parent.path / name) as child:
                        directories[relative] = identity(info)[:2]
                        collect(child, relative + '/')
                else:
                    raise CleanupIssue('unexpected_entry', '捕获暂存含未知条目')
    collect(root)
    return files, directories


def delete_staging(payload, expected_files, expected_dirs, run, *, reserved=False):
    from .cache_retirement import rmdir
    limit = len(expected_files) + len(expected_dirs) if reserved else None
    files, dirs = staging_snapshot(payload, run, limit=limit)
    if (any(expected_files.get(name) != fact for name, fact in files.items())
            or any(expected_dirs.get(name) != fact for name, fact in dirs.items())):
        raise CleanupIssue('identity_changed', '捕获暂存内容已变化')

    for name, expected in sorted(files.items()):
        relative = Path(name)
        with ExitStack() as stack:
            parent = payload if len(relative.parts) == 1 else stack.enter_context(
                pinned_directory(payload.path / relative.parent))
            with cleanup_step(parent.path / relative.name, 'unlink'):
                parent.unlink(relative.name, expected)
            run.deleted(expected[2])
            with cleanup_step(parent.path, 'sync'):
                parent.sync()
    for name in sorted(dirs, key=lambda item: (-item.count('/'), item)):
        relative = Path(name)
        with ExitStack() as stack:
            parent = payload if len(relative.parts) == 1 else stack.enter_context(
                pinned_directory(payload.path / relative.parent))
            if identity(parent.info(relative.name))[:2] != expected_dirs[name]:
                raise CleanupIssue('identity_changed', '捕获暂存目录身份已变化')
            with cleanup_step(parent.path / relative.name, 'rmdir'):
                rmdir(parent, relative.name)
            with cleanup_step(parent.path, 'sync'):
                parent.sync()
