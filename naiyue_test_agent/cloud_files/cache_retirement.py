"""Rename a frozen package out of the live namespace before deleting bytes."""
import json
import os
from uuid import uuid4

from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue, cleanup_step, identity, pinned_directory
from naiyue_test_agent.storage.cache_cleanup_inputs import INPUT_ID
from naiyue_test_agent.storage.files import open_store_file


def entries(directory, run, *, limit=None):
    names = set()
    with directory.entries() as listing:
        for entry in listing:
            if limit is None:
                run.check(path=directory.path)
            elif len(names) >= limit:
                raise CleanupIssue('unexpected_entry', '退役目录出现新增条目')
            names.add(entry.name)
    return names


def move(source, name, destination):
    source.verify()
    destination.verify()
    if source.fd is None:
        os.rename(source.path / name, destination.path / 'payload')
    else:
        os.rename(name, 'payload', src_dir_fd=source.fd, dst_dir_fd=destination.fd)


def rmdir(parent, name):
    parent.verify()
    if parent.fd is None:
        (parent.path / name).rmdir()
    else:
        os.rmdir(name, dir_fd=parent.fd)


def retire(inputs, staging, unit, original, files, run, limit, *, directories=None):
    name = f'cleanup-{uuid4()}'
    record = dict(format=1, request_id=run.request_id, input_id=unit, directory=original, files=files)
    if directories is not None:
        record['directories'] = directories
    payload = json.dumps(record, separators=(',', ':')).encode()
    if len(payload) > limit:
        raise CleanupIssue('metadata_unavailable', '退役清单超出大小限制', skipped=False)
    entry_count = len(files) + len(directories or {})
    run.check(2 + 2 * entry_count, path=staging.path / name / 'payload', unit=unit)
    staging.verify()
    if staging.fd is None:
        (staging.path / name).mkdir(mode=0o700)
    else:
        os.mkdir(name, mode=0o700, dir_fd=staging.fd)
    with pinned_directory(staging.path / name) as control:
        fd = open_store_file(control.path / 'cleanup.json', exclusive_create=True)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        control.sync()
        staging.sync()
        with pinned_directory(inputs.path / unit) as old:
            if identity(inputs.info(unit))[:2] != original:
                raise CleanupIssue('identity_changed', '目录在退役前已经变化')
            if directories is None:
                if entries(old, run, limit=entry_count) != files.keys() or any(
                        identity(old.info(filename)) != expected for filename, expected in files.items()):
                    raise CleanupIssue('identity_changed', '输入包在退役前已经变化')
            else:
                from .cache_staging import staging_snapshot
                if staging_snapshot(old, run, limit=entry_count) != (files, directories):
                    raise CleanupIssue('identity_changed', '捕获暂存已变化')
        move(inputs, unit, control)
        with cleanup_step(inputs.path, 'sync'):
            inputs.sync()
        with cleanup_step(control.path, 'sync'):
            control.sync()
    try:
        remove_payload(staging, name, run, limit, reserved=True)
    except (OSError, CleanupIssue, ValueError, TypeError, KeyError) as error:
        if not hasattr(error, 'cleanup_path'):
            error.cleanup_path = staging.path / name
            error.cleanup_step = 'finalize'
        raise


def remove_payload(staging, name, run, limit, *, reserved=False):
    with pinned_directory(staging.path / name) as control:
        children = entries(control, run, limit=2 if reserved else None)
        if not children:
            pass
        else:
            if not children <= {'cleanup.json', 'payload'} or 'cleanup.json' not in children:
                raise CleanupIssue('unexpected_entry', '退役目录含未知或缺少必要记录')
            record = json.loads(control.read('cleanup.json', limit))
            unit, snapshot = record['input_id'], record['files']
            directories = record.get('directories')
            from .cache_staging import STAGING_NAME, allowed, delete_staging
            if record['format'] != 1 or not isinstance(snapshot, dict):
                raise CleanupIssue('metadata_unavailable', '退役记录无法识别', skipped=False)
            if directories is None:
                from .cache_cleanup import JOURNAL, PACKAGE_FILES, UPLOAD_LOCK
                if not INPUT_ID.fullmatch(unit) or not PACKAGE_FILES <= snapshot.keys() or any(
                        filename not in PACKAGE_FILES and filename != 'upload.lock'
                        and not JOURNAL.fullmatch(filename) and not UPLOAD_LOCK.fullmatch(filename)
                        for filename in snapshot):
                    raise CleanupIssue('unexpected_entry', '退役清单含未知文件')
            elif not STAGING_NAME.fullmatch(unit) or not isinstance(directories, dict) or any(
                    not allowed(path, is_dir) for is_dir, mapping in ((True, directories), (False, snapshot))
                    for path in mapping):
                raise CleanupIssue('unexpected_entry', '捕获暂存退役清单格式无效')
            if 'payload' in children:
                with pinned_directory(control.path / 'payload') as payload:
                    if identity(control.info('payload'))[:2] != record['directory']:
                        raise CleanupIssue('identity_changed', '退役包目录身份已变化')
                    if directories is not None:
                        delete_staging(payload, snapshot, directories, run, reserved=reserved)
                    else:
                        names = entries(payload, run, limit=len(snapshot) if reserved else None)
                        if not names <= snapshot.keys():
                            raise CleanupIssue('unexpected_entry', '退役包发现未知文件')
                        for filename in sorted(names):
                            if identity(payload.info(filename)) != snapshot[filename]:
                                raise CleanupIssue('identity_changed', '退役包的文件已变化')
                        for filename in sorted(names):
                            with cleanup_step(payload.path / filename, 'unlink'):
                                payload.unlink(filename, snapshot[filename])
                            if not filename.endswith('.lock'):
                                run.deleted(snapshot[filename][2])
                        with cleanup_step(payload.path, 'sync'):
                            payload.sync()
                if identity(control.info('payload'))[:2] != record['directory']:
                    raise CleanupIssue('identity_changed', '退役目录身份已变化')
                with cleanup_step(control.path / 'payload', 'rmdir'):
                    rmdir(control, 'payload')
            with cleanup_step(control.path / 'cleanup.json', 'unlink'):
                control.unlink('cleanup.json', identity(control.info('cleanup.json')))
            with cleanup_step(control.path, 'sync'):
                control.sync()
    with cleanup_step(staging.path / name, 'rmdir'):
        rmdir(staging, name)
    with cleanup_step(staging.path, 'sync'):
        staging.sync()
