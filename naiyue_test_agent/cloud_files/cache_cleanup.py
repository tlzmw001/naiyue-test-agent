"""Age-based cleanup of published local input packages."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re

from naiyue_test_agent.contracts.cloud_inputs import CapturedInput, CreateTransfer, InputManifest, InputPackage, Selection
from naiyue_test_agent.contracts.cloud_tasks import UUIDText
from naiyue_test_agent.storage.cache_cleanup_files import CleanupIssue, identity, pinned_directory, regular
from naiyue_test_agent.storage.cache_cleanup_inputs import INPUT_ID, input_dependencies
from naiyue_test_agent.storage.files import acquire_store_lock, open_store_file, release_store_lock
from pydantic import TypeAdapter

from .cache_access import SIGNATURE, readers, validate_cache
from .cache_retirement import remove_payload, retire
from .cache_staging import STAGING_NAME, staging_snapshot

PACKAGE_FILES = {'archive', 'input.json', 'manifest.json', 'selection.json', 'receipt.json'}
JOURNAL = re.compile(r'upload-([a-f0-9-]{36})(?:-([a-f0-9-]{36}))?\.json')
UPLOAD_LOCK = re.compile(r'upload-([a-f0-9-]{36})\.lock')
UUID = TypeAdapter(UUIDText)


def package_files(directory, cache, ident, run, metadata_limit):
    files = {}
    with directory.entries() as entries:
        for entry in entries:
            run.check(path=directory.path, unit=ident)
            name = entry.name
            data = directory.info(name)
            if not regular(data):
                raise CleanupIssue('unexpected_entry', '输入包内有非普通文件')
            journal, lock = JOURNAL.fullmatch(name), UPLOAD_LOCK.fullmatch(name)
            if name not in PACKAGE_FILES and name != 'upload.lock' and not (journal or lock):
                raise CleanupIssue('unexpected_entry', '输入包内有未知文件')
            for uid in (journal or lock).groups() if journal or lock else ():
                if uid is not None:
                    UUID.validate_python(uid)
            files[name] = identity(data)
    if not PACKAGE_FILES <= files.keys():
        raise CleanupIssue('metadata_unavailable', '输入包缺少必要文件', skipped=False)
    facts = InputPackage.model_validate_json(directory.read('input.json', metadata_limit * 4))
    receipt = CapturedInput.model_validate_json(directory.read('receipt.json', metadata_limit))
    manifest = InputManifest.model_validate_json(directory.read('manifest.json', metadata_limit))
    selection = Selection.model_validate_json(directory.read('selection.json', metadata_limit))
    expected = (ident, facts.archive_sha256, facts.size, facts.codec)
    if (facts.input_id != ident or manifest.input_id != ident or facts.manifest != manifest
            or facts.selection != selection or receipt.directory != cache / 'inputs' / ident
            or files['archive'][2] != facts.size
            or (receipt.input_id, receipt.archive_sha256, receipt.size, receipt.codec) != expected):
        raise CleanupIssue('identity_changed', '包的保存路径或元数据不一致')
    for name in files:
        if JOURNAL.fullmatch(name):
            journal = CreateTransfer.model_validate_json(directory.read(name, 8192))
            if (journal.input_id, journal.archive_sha256, journal.size, journal.codec) != expected:
                raise CleanupIssue('identity_changed', '上传日志与包身份不一致')
    return files


def clean_input_cache(path, run, *, host, connection, metadata_limit):
    run.counts['scopes_visited'] += 1
    try:
        path.lstat()
    except FileNotFoundError:
        return
    with ExitStack() as stack:
        cache = stack.enter_context(pinned_directory(path))
        validate_cache(cache)
        lock_info = cache.info('cache.lock')
        if not regular(lock_info):
            raise CleanupIssue('ownership_invalid', '输入缓存锁无效')
        fd = open_store_file(cache.path / 'cache.lock')
        stack.callback(os.close, fd)
        try:
            acquire_store_lock(fd)
            stack.callback(release_store_lock, fd)
            os.lseek(fd, 0, os.SEEK_SET)
            if identity(lock_info)[:2] != identity(os.fstat(fd))[:2] or os.read(fd, 128) != SIGNATURE:
                raise CleanupIssue('ownership_invalid', '输入缓存锁标记不匹配')
            stack.enter_context(readers(cache, exclusive=True))
        except BlockingIOError as error:
            raise CleanupIssue('input_cache_busy', '输入包正在捕获、上传或释放') from error
        validate_cache(cache)
        legacy, import_links = {}, {}
        if host is None:
            packages, imports = input_dependencies(connection, path, run.scope_id, run,
                legacy_checks=legacy, import_links=import_links)
        else:
            with host.cloud_tasks.repo.database.transaction() as db:
                packages, imports = input_dependencies(db, path, run.scope_id, run,
                    legacy_checks=legacy, import_links=import_links)
        for ident, dependencies in legacy.items():
            if all(isinstance(package, dict) and checked_legacy_archive(path.parent, ident, command, package, run)
                   for command, package in dependencies):
                packages.pop(ident)
        inputs = stack.enter_context(pinned_directory(path / 'inputs'))
        staging = stack.enter_context(pinned_directory(path / 'staging'))
        imported = stack.enter_context(pinned_directory(path / 'imports'))
        for directory in (staging, inputs, imported):
            names = []
            with directory.entries() as entries:
                for entry in entries:
                    run.check(path=directory.path)
                    names.append(entry.name)
            for name in sorted(names):
                target = directory.path / name
                run.check(path=target, unit=name)
                run.counts['units_examined'] += 1
                step = 'read_metadata'
                try:
                    if directory is staging:
                        if name.startswith('cleanup-'):
                            UUID.validate_python(name.removeprefix('cleanup-'))
                            step = 'finalize'
                            remove_payload(staging, name, run, metadata_limit)
                        else:
                            if not STAGING_NAME.fullmatch(name):
                                raise CleanupIssue('unexpected_entry', '无法核验此捕获暂存')
                            with pinned_directory(path / 'staging' / name) as abandoned:
                                original = identity(staging.info(name))[:2]
                                files, directories = staging_snapshot(abandoned, run)
                            if not files:
                                raise CleanupIssue('unexpected_entry', '暂存没有可核验的文件时间')
                            if max(fact[3] for fact in files.values()) >= run.cutoff:
                                run.counts['units_retained'] += 1
                                continue
                            step = 'retire'
                            retire(staging, staging, name, original, files, run, metadata_limit,
                                   directories=directories)
                    elif directory is inputs:
                        if not INPUT_ID.fullmatch(name):
                            raise CleanupIssue('unexpected_entry', '未知输入目录')
                        if name in packages:
                            code = 'recovery_required' if packages[name].startswith('旧交付 ') else 'input_in_use'
                            step = 'check_dependencies'
                            raise CleanupIssue(code, packages[name])
                        with pinned_directory(path / 'inputs' / name) as package:
                            original = identity(inputs.info(name))[:2]
                            files = package_files(package, path, name, run, metadata_limit)
                        age = max(info[3] for key, info in files.items() if not key.endswith('.lock'))
                        if age >= run.cutoff:
                            run.counts['units_retained'] += 1
                            continue
                        step = 'retire'
                        retire(inputs, staging, name, original, files, run, metadata_limit)
                    else:
                        UUID.validate_python(Path(name).stem)
                        if Path(name).suffix != '.json':
                            raise CleanupIssue('unexpected_entry', '未知来源回执')
                        if name in imports:
                            step = 'check_dependencies'
                            raise CleanupIssue('input_in_use', imports[name])
                        for linked_id in import_links.get(name, ()):
                            run.check(path=target)
                            if not INPUT_ID.fullmatch(linked_id):
                                raise CleanupIssue('metadata_unavailable', '来源回执的输入身份无效')
                            try:
                                inputs.info(linked_id)
                            except FileNotFoundError:
                                continue
                            step = 'check_dependencies'
                            raise CleanupIssue('input_in_use', '来源回执关联的输入包仍保留')
                        from naiyue_test_agent.contracts.cloud_sources import SourceReceipt
                        info = directory.info(name)
                        SourceReceipt.model_validate_json(directory.read(name, metadata_limit * 4))
                        if info.st_mtime_ns >= run.cutoff:
                            run.counts['units_retained'] += 1
                            continue
                        step = 'unlink'
                        directory.unlink(name, identity(info))
                        run.deleted(info.st_size)
                        step = 'sync'
                        directory.sync()
                    run.counts['units_deleted'] += 1
                except (OSError, CleanupIssue, ValueError, KeyError, TypeError) as error:
                    run.issue(getattr(error, 'cleanup_path', target), getattr(error, 'cleanup_step', step), error, name)


def checked_legacy_archive(project_path, ident, command, package, run):
    from naiyue_test_agent.storage.files import open_store_readonly
    try:
        UUID.validate_python(command)
        expected = InputPackage.model_validate(package)
        if expected.input_id != ident:
            return False
        with pinned_directory(project_path / 'cloud-results' / 'deliveries' / command) as directory:
            status = directory.info('archive')
            if not regular(status) or status.st_size != expected.size:
                return False
            fd = open_store_readonly(directory.path / 'archive')
            with os.fdopen(fd, 'rb') as stream:
                if identity(os.fstat(fd)) != identity(status):
                    return False
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    run.check()
                    digest.update(chunk)
                return (identity(os.fstat(fd)) == identity(status)
                        and identity(directory.info('archive')) == identity(status)
                        and digest.hexdigest() == expected.archive_sha256)
    except (OSError, CleanupIssue, ValueError):
        # The caller retains the original package and reports recovery_required.
        return False
