"""Bounded Bash output artifacts inside one local workspace."""

import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import uuid4


logger = logging.getLogger(__name__)
OUTPUT_DIR = 'naiyue-agent-output'
_OWNER_FILE = '.naiyue-bash-output-v1'
_OWNER_TEXT = 'naiyue-test-agent bash output v1\n'
_IGNORE_TEXT = '*\n'
_NAME = re.compile(r'[0-9a-f]{32}-(stdout|stderr)\.log\Z')
_GLOBAL_LOCK = Lock()
_DEFAULT_COMMAND_BYTES = 16 * 1024 * 1024
_DEFAULT_WORKSPACE_BYTES = 128 * 1024 * 1024
_TAIL_BYTES = 4096


@dataclass(frozen=True)
class ArchiveResult:
    paths: dict[str, str]
    complete: bool
    reason: str | None


@dataclass(frozen=True)
class ClearPlan:
    root: Path
    files: tuple[tuple[str, int, int, int], ...]

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(item[1] for item in self.files)


def _positive_limit(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        limit = int(value)
    except ValueError as error:
        raise ValueError(f'{name} must be a positive integer') from error
    if limit <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return limit


def _write_owned_file(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
        target.write(content)


def _directory(root: Path, *, create: bool) -> Path | None:
    directory = root / OUTPUT_DIR
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        if not create:
            return None
        directory.mkdir(mode=0o700)
        _write_owned_file(directory / '.gitignore', _IGNORE_TEXT)
        _write_owned_file(directory / _OWNER_FILE, _OWNER_TEXT)
        mode = directory.lstat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise ValueError('output directory is not private')
    if (directory / _OWNER_FILE).is_symlink() or (directory / '.gitignore').is_symlink():
        raise ValueError('output directory marker is a symlink')
    if ((directory / _OWNER_FILE).read_text(encoding='utf-8') != _OWNER_TEXT
            or (directory / '.gitignore').read_text(encoding='utf-8') != _IGNORE_TEXT):
        raise ValueError('output directory is not owned by this application')
    return directory


def _snapshot(directory: Path) -> tuple[tuple[str, int, int, int], ...]:
    files = []
    for path in directory.iterdir():
        if path.name in ('.gitignore', _OWNER_FILE):
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('output directory contains a symlink')
        if not _NAME.fullmatch(path.name) or not stat.S_ISREG(info.st_mode):
            raise ValueError('output directory contains an unexpected file')
        files.append((path.name, info.st_size, info.st_mtime_ns, info.st_ino))
    return tuple(sorted(files))


def preview_bash_outputs(root: Path) -> ClearPlan:
    """Return the exact owned-file snapshot that a CLI user can review."""
    with _GLOBAL_LOCK:
        directory = _directory(root, create=False)
        return ClearPlan(root, _snapshot(directory) if directory is not None else ())


def clear_bash_outputs(plan: ClearPlan) -> tuple[int, int]:
    """Delete only files from an unchanged, previously displayed plan."""
    with _GLOBAL_LOCK:
        directory = _directory(plan.root, create=False)
        current = _snapshot(directory) if directory is not None else ()
        if current != plan.files:
            raise ValueError('output files changed since preview')
        if directory is not None:
            for name, _size, _mtime, _inode in current:
                (directory / name).unlink()
        return plan.file_count, plan.total_bytes


class BashOutputArchive:
    """Keep a short in-memory prefix, then spool both raw streams on overflow."""

    def __init__(self, root: Path):
        self._root = root
        self._command_limit = _positive_limit('NAIYUE_BASH_OUTPUT_COMMAND_BYTES',
                                               _DEFAULT_COMMAND_BYTES)
        self._workspace_limit = _positive_limit('NAIYUE_BASH_OUTPUT_WORKSPACE_BYTES',
                                                 _DEFAULT_WORKSPACE_BYTES)
        self._lock = Lock()
        self._prefix = {'stdout': bytearray(), 'stderr': bytearray()}
        self._handles: dict[str, object] = {}
        self._paths: dict[str, str] = {}
        self._stem = uuid4().hex
        self._started = False
        self._closed = False
        self._written = 0
        self._reason: str | None = None

    def record(self, stream: str, chunk: bytes) -> None:
        with self._lock:
            if self._closed or self._reason is not None:
                return
            if not self._started:
                self._prefix[stream].extend(chunk)
                if any(len(data) > _TAIL_BYTES for data in self._prefix.values()):
                    self._started = True
                    for name, prefix in self._prefix.items():
                        if prefix:
                            self._write(name, bytes(prefix))
                            prefix.clear()
                return
            self._write(stream, chunk)

    def _write(self, stream: str, data: bytes) -> None:
        if self._reason is not None:
            return
        try:
            with _GLOBAL_LOCK:
                directory = _directory(self._root, create=True)
                existing = _snapshot(directory)
                used = sum(item[1] for item in existing)
                command_remaining = self._command_limit - self._written
                workspace_remaining = self._workspace_limit - used
                allowed = min(len(data), command_remaining, workspace_remaining)
                if allowed > 0:
                    handle = self._handles.get(stream)
                    if handle is None:
                        name = f'{self._stem}-{stream}.log'
                        descriptor = os.open(directory / name,
                                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        handle = os.fdopen(descriptor, 'wb', buffering=0)
                        self._handles[stream] = handle
                        self._paths[stream] = f'{OUTPUT_DIR}/{name}'
                    handle.write(data[:allowed])
                    self._written += allowed
                if allowed < len(data):
                    self._reason = ('command_limit' if command_remaining <= workspace_remaining
                                    else 'workspace_limit')
        except (OSError, ValueError, UnicodeError) as error:
            self._reason = ('directory_unavailable' if not self._paths else 'write_error')
            logger.warning('bash_archive_failed error_type=%s', type(error).__name__)

    def finish(self) -> ArchiveResult:
        with self._lock:
            self._closed = True
            for handle in self._handles.values():
                try:
                    handle.close()
                except OSError as error:
                    self._reason = 'write_error'
                    logger.warning('bash_archive_close_failed error_type=%s',
                                   type(error).__name__)
            return ArchiveResult(dict(self._paths), self._started and self._reason is None,
                                 self._reason)

    def mark_incomplete(self, reason: str) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason
