"""Owned, quota-bounded Shell artifacts shared by Bash and PowerShell."""

import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .output_text import OutputAccumulator


logger = logging.getLogger(__name__)
OUTPUT_DIR = 'naiyue-agent-output'
_OWNER_FILE = '.naiyue-bash-output-v1'
_OWNER_TEXT = 'naiyue-test-agent bash output v1\n'
_IGNORE_TEXT = '*\n'
_NAME = re.compile(r'[0-9a-f]{32}-(stdout|stderr|raw|text)\.log\Z')
_GLOBAL_LOCK = Lock()


@dataclass(frozen=True)
class ArchiveResult:
    paths: dict[str, str]
    complete: bool
    reason: str | None
    saved_bytes: int = 0


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


def _write_owned_file(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
        target.write(content)


def _directory(root: Path, *, create: bool) -> Path | None:
    directory = root / OUTPUT_DIR
    try:
        info = directory.lstat()
    except FileNotFoundError:
        if not create:
            return None
        directory.mkdir(mode=0o700)
        _write_owned_file(directory / '.gitignore', _IGNORE_TEXT)
        _write_owned_file(directory / _OWNER_FILE, _OWNER_TEXT)
        info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError('output directory is not a regular directory')
    for marker in (directory / _OWNER_FILE, directory / '.gitignore'):
        marker_info = marker.lstat()
        if stat.S_ISLNK(marker_info.st_mode):
            raise ValueError('output directory marker is a symlink')
        if (not stat.S_ISREG(marker_info.st_mode)
                or getattr(marker_info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('output directory marker is not a regular file')
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
        if (not _NAME.fullmatch(path.name) or not stat.S_ISREG(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('output directory contains an unexpected file')
        files.append((path.name, info.st_size, info.st_mtime_ns, info.st_ino))
    return tuple(sorted(files))


def preview_shell_outputs(root: Path) -> ClearPlan:
    """Return the exact owned-file snapshot that a CLI user can review."""
    with _GLOBAL_LOCK:
        directory = _directory(root, create=False)
        return ClearPlan(root, _snapshot(directory) if directory is not None else ())


def clear_shell_outputs(plan: ClearPlan) -> tuple[int, int]:
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


class ShellOutputArchive:
    """Spool the buffered prefix on overflow; derive a text view only if needed."""

    def __init__(self, root: Path, settings, output_settings):
        self._root = root
        self._settings = settings
        self._command_limit = settings.max_archive_bytes
        self._workspace_limit = output_settings.max_workspace_archive_bytes
        self._lock = Lock()
        self._prefix = bytearray()
        self._handles: dict[str, object] = {}
        self._paths: dict[str, str] = {}
        self._stem = uuid4().hex
        self._started = False
        self._closed = False
        self._written = 0
        self._reason: str | None = None
        self._blocked = False

    def record(self, chunk: bytes, *, activate: bool) -> None:
        with self._lock:
            if self._closed or self._blocked:
                return
            if not self._started:
                self._prefix.extend(chunk)
                if activate:
                    self._activate()
            else:
                self._write('raw', chunk)

    def _activate(self):
        if not self._started:
            self._started = True
            self._write('raw', bytes(self._prefix))
            self._prefix.clear()

    def activate(self, needed):
        with self._lock:
            if needed and not self._closed:
                self._activate()

    def _write(self, stream: str, data: bytes) -> None:
        if self._blocked or not data:
            return
        try:
            with _GLOBAL_LOCK:
                directory = _directory(self._root, create=True)
                existing = _snapshot(directory)
                used = sum(item[1] for item in existing)
                command_remaining = self._command_limit - self._written
                workspace_remaining = self._workspace_limit - used
                allowed = min(len(data), command_remaining, workspace_remaining)
                if stream == 'text' and allowed < len(data):
                    allowed = len(data[:allowed].decode('utf-8', errors='ignore').encode('utf-8'))
                if allowed > 0:
                    handle = self._handles.get(stream)
                    if handle is None:
                        name = f'{self._stem}-{stream}.log'
                        descriptor = os.open(directory / name,
                                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        handle = os.fdopen(descriptor, 'wb', buffering=0)
                        self._handles[stream] = handle
                        self._paths[stream] = f'{OUTPUT_DIR}/{name}'
                    # FileIO may return a partial write. Count only actual bytes.
                    view = memoryview(data)[:allowed]
                    while view:
                        written = handle.write(view)
                        if not written:
                            raise OSError('Archive write made no progress')
                        self._written += written
                        view = view[written:]
                if allowed < len(data):
                    self._reason = ('command_limit' if command_remaining <= workspace_remaining
                                    else 'workspace_limit')
                    self._blocked = True
        except (OSError, ValueError, UnicodeError) as error:
            self._reason = ('directory_unavailable' if not self._paths else 'write_error')
            self._blocked = True
            logger.warning('shell_archive_failed error_type=%s', type(error).__name__)

    def finish(self, *, transformed=False) -> ArchiveResult:
        with self._lock:
            if self._closed:
                return self._result()
            if transformed and not self._started:
                self._activate()
            if transformed and 'raw' in self._paths and not self._blocked:
                self._make_text_view()
            self._closed = True
            for handle in self._handles.values():
                try:
                    handle.close()
                except OSError as error:
                    self._reason = 'write_error'
                    logger.warning('shell_archive_close_failed error_type=%s',
                                   type(error).__name__)
            self._prefix.clear()
            return self._result()

    def _result(self):
        return ArchiveResult(dict(self._paths), self._started and self._reason is None,
                             self._reason, self._written)

    def _make_text_view(self):
        # Streaming replay avoids retaining the whole prefix when a late control
        # sequence first makes raw and text differ. Both files consume one quota.
        normalizer = OutputAccumulator(self._settings)
        try:
            with (self._root / self._paths['raw']).open('rb') as source:
                while chunk := source.read(65536):
                    self._write('text', normalizer.feed(chunk).encode('utf-8'))
                    if self._blocked:
                        return
                self._write('text', normalizer.finish().encode('utf-8'))
        except (OSError, UnicodeError) as error:
            self._reason = 'read_error'
            self._blocked = True
            logger.warning('shell_archive_read_failed error_type=%s', type(error).__name__)

    def mark_incomplete(self, reason: str) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason
