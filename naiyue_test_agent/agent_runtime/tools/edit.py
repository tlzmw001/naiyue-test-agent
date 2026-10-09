"""Exact edits to existing UTF-8 files inside a trusted session workspace."""

import codecs
import hashlib
import logging
import os
import stat
import tempfile
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field

from .write import (
    WRITE_BUSY, WRITE_CHANGED, WRITE_IO_FAILURE, WRITE_LIMIT, WriteOutcomeUnknown,
    _replace_temp, _reserve_target, _target_in_workspace, _write_limit,
)


logger = logging.getLogger(__name__)
EDIT_UNAVAILABLE = ('edit cannot access this file: it may be missing, outside the '
                    'workspace, or blocked by workspace policy. Choose an existing '
                    'workspace text file.')
EDIT_ENCODING = ('edit requires a valid UTF-8 text file. Use another tool for '
                 'binary files or unsupported encodings.')
EDIT_LIMIT = ('edit replacement text exceeds the configured input limit. Shorten '
              'oldText/newText in this call and retry.')
EDIT_NOT_FOUND = ('edit oldText was not found exactly. Read the file again and '
                  'include the exact whitespace and newlines.')
EDIT_NOT_UNIQUE = ('edit oldText is not unique. Include enough surrounding text '
                   'to identify one location.')
EDIT_OVERLAP = ('edit replacements overlap. Merge the affected blocks into one '
                'replacement or choose disjoint text.')
EDIT_EMPTY = ('edit oldText cannot be empty. Provide text from the existing file.')
EDIT_NO_CHANGE = ('edit made no change. Provide replacement text that differs '
                  'from the current file.')
EDIT_NO_WORKSPACE = ('edit has no workspace for this session. Select a workspace '
                     'before editing files.')
EDIT_INVALID_ARGS = ('Invalid edit arguments. Provide path and a non-empty edits '
                     'list of oldText/newText strings; remove unknown fields.')
EDIT_IO_FAILURE = ('edit could not read or prepare the file due to an I/O error. '
                   'Check workspace permissions or free space before trying again.')
EDIT_TOOL_FAILURE = ('edit failed unexpectedly. Read the file before trying again.')
SAFE_EDIT_ERRORS = frozenset((EDIT_UNAVAILABLE, EDIT_ENCODING, EDIT_LIMIT,
                              EDIT_NOT_FOUND, EDIT_NOT_UNIQUE, EDIT_OVERLAP,
                              EDIT_EMPTY, EDIT_NO_CHANGE, EDIT_NO_WORKSPACE,
                              EDIT_INVALID_ARGS, EDIT_IO_FAILURE, EDIT_TOOL_FAILURE,
                              WRITE_BUSY, WRITE_CHANGED, WRITE_IO_FAILURE, WRITE_LIMIT))


class EditReplacement(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    oldText: str
    newText: str


class EditInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(min_length=1, max_length=4096)
    edits: list[EditReplacement] = Field(min_length=1)


class SessionEditInput(EditInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)

    runtime: ToolRuntime[dict, dict]


_CHUNK_BYTES = 64 * 1024
_UTF8_BOM = b'\xef\xbb\xbf'


def _stamp(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns)


def _scan_target(target: Path, old_parts: list[bytes]):
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    digest = hashlib.sha256()
    locations: list[int | None] = [None] * len(old_parts)
    overlap = max(len(part) for part in old_parts) - 1
    with target.open('rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('not_regular_file')
        head = source.read(len(_UTF8_BOM))
        if head == _UTF8_BOM:
            digest.update(head)
            decoder.decode(head)
            offset = len(head)
        else:
            source.seek(0)
            offset = 0
        tail = b''
        while chunk := source.read(_CHUNK_BYTES):
            digest.update(chunk)
            decoder.decode(chunk)
            window = tail + chunk
            base = offset - len(tail)
            for index, part in enumerate(old_parts):
                found = window.find(part)
                while found >= 0:
                    start = base + found
                    if start + len(part) > offset:
                        if locations[index] is not None:
                            raise ToolException(EDIT_NOT_UNIQUE)
                        locations[index] = start
                    found = window.find(part, found + 1)
            offset += len(chunk)
            tail = window[-overlap:] if overlap else b''
        decoder.decode(b'', final=True)
        if _stamp(os.fstat(source.fileno())) != _stamp(before):
            raise ToolException(WRITE_CHANGED)
    if any(location is None for location in locations):
        raise ToolException(EDIT_NOT_FOUND)
    return locations, digest.digest(), offset, _stamp(before), stat.S_IMODE(before.st_mode)


def _copy_to_temp(
    root: Path, path: str, target: Path, matches: list[tuple[int, int, bytes]],
    original_digest: bytes, original_size: int, original_stamp: tuple[int, ...],
    original_mode: int,
) -> int:
    temporary: Path | None = None
    try:
        try:
            if _target_in_workspace(root, path) != target:
                raise ToolException(WRITE_CHANGED)
            with target.open('rb') as source, tempfile.NamedTemporaryFile(
                mode='wb', prefix='.naiyue-write-', dir=target.parent, delete=False,
            ) as output:
                temporary = Path(output.name)
                if _stamp(os.fstat(source.fileno())) != original_stamp:
                    raise ToolException(WRITE_CHANGED)
                digest = hashlib.sha256()
                position = 0
                output_size = 0

                def transfer(stop: int, *, copy: bool) -> None:
                    nonlocal position, output_size
                    while position < stop:
                        chunk = source.read(min(_CHUNK_BYTES, stop - position))
                        if not chunk:
                            raise ToolException(WRITE_CHANGED)
                        digest.update(chunk)
                        position += len(chunk)
                        if copy:
                            output.write(chunk)
                            output_size += len(chunk)

                for start, end, replacement in matches:
                    transfer(start, copy=True)
                    transfer(end, copy=False)
                    output.write(replacement)
                    output_size += len(replacement)
                while chunk := source.read(_CHUNK_BYTES):
                    digest.update(chunk)
                    output.write(chunk)
                    position += len(chunk)
                    output_size += len(chunk)
                if (position != original_size or digest.digest() != original_digest
                        or _stamp(os.fstat(source.fileno())) != original_stamp):
                    raise ToolException(WRITE_CHANGED)
                os.fchmod(output.fileno(), original_mode & 0o777)
                output.flush()
                os.fsync(output.fileno())
            if (_target_in_workspace(root, path) != target
                    or _stamp(target.stat()) != original_stamp):
                raise ToolException(WRITE_CHANGED)
        except (OSError, ValueError, RuntimeError) as error:
            logger.warning('edit_failed phase=prepare error_type=%s', type(error).__name__)
            raise ToolException(EDIT_IO_FAILURE) from error
        _replace_temp(temporary, target)
        temporary = None
        return output_size
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                logger.warning('edit_temp_cleanup_failed error_type=%s',
                               type(error).__name__)


def _edit(root: Path, path: str, edits: list[EditReplacement], max_bytes: int) -> str:
    if sum(len(item.oldText) + len(item.newText) for item in edits) > max_bytes:
        logger.warning('edit_failed phase=limit error_type=InputLimit')
        raise ToolException(EDIT_LIMIT)
    try:
        old_parts = [item.oldText.encode('utf-8') for item in edits]
        new_parts = [item.newText.encode('utf-8') for item in edits]
    except UnicodeError as error:
        logger.warning('edit_failed phase=encode error_type=%s', type(error).__name__)
        raise ToolException(EDIT_ENCODING) from error
    if sum(len(old) + len(new) for old, new in zip(old_parts, new_parts)) > max_bytes:
        logger.warning('edit_failed phase=limit error_type=InputLimit')
        raise ToolException(EDIT_LIMIT)
    if any(not old for old in old_parts):
        raise ToolException(EDIT_EMPTY)
    try:
        target = _target_in_workspace(root, path)
        if not target.is_file():
            raise ValueError('missing_file')
    except (OSError, ValueError, RuntimeError) as error:
        logger.warning('edit_failed phase=read error_type=%s', type(error).__name__)
        raise ToolException(EDIT_UNAVAILABLE) from error
    with _reserve_target(target):
        try:
            locations, digest, size, stamp, mode = _scan_target(target, old_parts)
        except UnicodeError as error:
            logger.warning('edit_failed phase=decode error_type=%s', type(error).__name__)
            raise ToolException(EDIT_ENCODING) from error
        except (OSError, ValueError, RuntimeError) as error:
            logger.warning('edit_failed phase=scan error_type=%s', type(error).__name__)
            raise ToolException(EDIT_IO_FAILURE) from error
        matches = sorted((start, start + len(old), new)
                         for start, old, new in zip(locations, old_parts, new_parts))
        for previous, current in zip(matches, matches[1:]):
            if previous[1] > current[0]:
                raise ToolException(EDIT_OVERLAP)
        if all(old == new for old, new in zip(old_parts, new_parts)):
            raise ToolException(EDIT_NO_CHANGE)
        result_size = _copy_to_temp(root, path, target, matches, digest, size, stamp, mode)
    return f'edit replaced {len(edits)} block(s); {result_size} bytes'


def _safe_edit_error(error: ToolException) -> str:
    message = error.args[0] if error.args else None
    return message if isinstance(message, str) and message in SAFE_EDIT_ERRORS else EDIT_TOOL_FAILURE


def create_session_edit_tool():
    """Edit an existing UTF-8 file in the application-owned session workspace."""
    max_bytes = _write_limit()

    @tool('edit', args_schema=SessionEditInput)
    def edit(
        path: str, edits: list[EditReplacement], runtime: ToolRuntime[dict, dict],
    ) -> str:
        """Replace unique, non-overlapping exact text in one existing workspace file.

        Every oldText is matched against the original file. Read it first to get
        exact whitespace and newlines. This tool cannot create a file.
        """
        context = runtime.context
        root = context.get('workspace_root') if isinstance(context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('edit_failed phase=context error_type=MissingWorkspaceContext')
            raise ToolException(EDIT_NO_WORKSPACE)
        try:
            return _edit(root, path, edits, max_bytes)
        except WriteOutcomeUnknown:
            logger.error('edit_outcome_unknown', extra={'write_event': {
                'event': 'edit_outcome_unknown',
                'run_id': context.get('run_id'),
                'tool_call_id': runtime.tool_call_id,
            }})
            raise

    edit.handle_validation_error = EDIT_INVALID_ARGS
    edit.handle_tool_error = _safe_edit_error
    edit.metadata = {'naiyue_execution_mode': 'sequential'}
    return edit
