"""Age-based cleaning of shell output groups."""
from collections import defaultdict

from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue, identity, pinned_directory, regular
from .output import OUTPUT_DIR, _GLOBAL_LOCK, _NAME, _OWNER_FILE, _OWNER_TEXT, _IGNORE_TEXT


def markers(directory):
    return tuple((directory.read(name, limit), identity(directory.info(name)))
                 for name, limit in ((_OWNER_FILE, 128), ('.gitignore', 32)))


def clean_outputs(root, run):
    path = root / OUTPUT_DIR
    try:
        with pinned_directory(path) as directory:
            owner = markers(directory)
            if tuple(data.replace(b'\r\n', b'\n') for data, _ in owner) != (_OWNER_TEXT.encode(), _IGNORE_TEXT.encode()):
                raise CleanupIssue('ownership_invalid', '归档标记不匹配')
            groups = defaultdict(dict)
            with directory.entries() as entries:
                for entry in entries:
                    run.check()
                    name = entry.name
                    if name in (_OWNER_FILE, '.gitignore'):
                        continue
                    info = directory.info(name)
                    if not _NAME.fullmatch(name) or not regular(info):
                        run.issue(path / name, 'enumerate', CleanupIssue('unexpected_entry', '未知或非普通归档条目'))
                        return
                    groups[name[:32]][name] = info
            for unit, files in sorted(groups.items()):
                run.check(path=path, unit=unit)
                run.counts['units_examined'] += 1
                if max(info.st_mtime_ns for info in files.values()) >= run.cutoff:
                    run.counts['units_retained'] += 1
                    continue
                target, step = path, 'revalidate'
                try:
                    with _GLOBAL_LOCK:
                        if markers(directory) != owner:
                            run.issue(path, 'revalidate', CleanupIssue('ownership_invalid', '归档标记已变化'), unit)
                            return
                        for suffix in ('raw', 'text', 'stdout', 'stderr'):
                            name = f'{unit}-{suffix}.log'
                            try:
                                current = directory.info(name)
                            except FileNotFoundError:
                                if name in files:
                                    raise
                                continue
                            if name not in files or not regular(current) or identity(current) != identity(files[name]):
                                raise CleanupIssue('identity_changed', '归档组已变化')
                        step = 'unlink'
                        for name, info in sorted(files.items()):
                            target = path / name
                            directory.unlink(name, identity(info))
                            run.deleted(info.st_size)
                        step = 'sync'
                        directory.sync()
                    run.counts['units_deleted'] += 1
                except (OSError, CleanupIssue) as error:
                    run.issue(target, step, error, unit)
    except FileNotFoundError as error:
        if path.is_symlink() or path.exists():
            run.issue(path, 'read_metadata', error)
    except (OSError, CleanupIssue) as error:
        run.issue(path, 'enumerate', error)
