"""Exact edits to existing UTF-8 files inside a trusted session workspace."""

from naiyue_test_agent.contracts.run_context import RuntimeContext
import codecs
import hashlib
import logging
import os
import re
import stat
import tempfile
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field

from .write import (
    WRITE_CHANGED,
    _replace_temp, _reserve_target, _resolve_write_target,
)
from .schemas import build_tool_schemas
from .settings import EditSettings
from .registry import ToolSpec
from .permissions import FilePermission, require_file_target
from .paths import verify_file_target
from .edit_matching import (EDIT_NOT_UNIQUE, EDIT_STOPPED,
                            file_stamp, require_file_stamp, prepare_edit_plan)
from .edit_diff import EditFeedback, build_edit_feedback
from .control import check_tool_stop
from .progress import ToolProgress


logger = logging.getLogger(__name__)
EDIT_UNAVAILABLE = ('edit cannot access this file: it may be missing, outside the '
                    'workspace, or blocked by workspace policy. Choose an existing '
                    'workspace text file.')
EDIT_ENCODING = ('edit requires a valid UTF-8 text file. Use another tool for '
                 'binary files or unsupported encodings.')
EDIT_LIMIT = ('edit replacement text exceeds the configured input limit. Shorten '
              'oldText/newText in this call and retry.')
EDIT_EMPTY = ('edit oldText cannot be empty. Provide text from the existing file.')
EDIT_NO_CHANGE = ('edit made no change. Provide replacement text that differs '
                  'from the current file.')
EDIT_NO_WORKSPACE = ('edit has no workspace for this session. Select a workspace '
                     'before editing files.')
EDIT_INVALID_ARGS = ('Invalid edit arguments. Provide path and a non-empty edits '
                     'list of oldText/newText strings; remove unknown fields.')
EDIT_IO_FAILURE = ('edit could not read or prepare the file due to an I/O error. '
                   'Check workspace permissions or free space before trying again.')


class EditReplacement(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    oldText: str
    newText: str


class _EditInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(min_length=1)
    edits: list[EditReplacement] = Field(min_length=1)


_CHUNK_BYTES = 64 * 1024
_UTF8_BOM = b'\xef\xbb\xbf'


def _scan_target(target: Path, old_parts: list[bytes], *, control=None, progress=None):
    check_tool_stop(control, EDIT_STOPPED)
    progress = progress or ToolProgress(None)
    progress.report(phase='scan_exact', completed=0, unit='bytes')
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    digest = hashlib.sha256()
    locations: list[int | None] = [None] * len(old_parts)
    overlap = max(len(part) for part in old_parts) - 1
    newline, previous_byte = None, b''
    with target.open('rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('not_regular_file')
        stamp = file_stamp(before)
        head = source.read(len(_UTF8_BOM))
        check_tool_stop(control, EDIT_STOPPED)
        if head == _UTF8_BOM:
            digest.update(head)
            decoder.decode(head)
            offset = len(head)
        else:
            source.seek(0)
            offset = 0
        tail = b''
        while chunk := source.read(_CHUNK_BYTES):
            check_tool_stop(control, EDIT_STOPPED)
            digest.update(chunk)
            decoder.decode(chunk)
            if newline is None:
                found_newline = re.search(b'\r\n|\r(?!$)|\n', previous_byte + chunk)
                if found_newline:
                    newline = found_newline.group()
            previous_byte = chunk[-1:]
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
            progress.report(phase='scan_exact', completed=offset, unit='bytes')
            tail = window[-overlap:] if overlap else b''
            check_tool_stop(control, EDIT_STOPPED)
        check_tool_stop(control, EDIT_STOPPED)
        decoder.decode(b'', final=True)
        require_file_stamp(os.fstat(source.fileno()), stamp)
    if newline is None and previous_byte == b'\r':
        newline = b'\r'
    check_tool_stop(control, EDIT_STOPPED)
    return locations, digest.digest(), offset, stamp, stat.S_IMODE(before.st_mode), newline


def _copy_to_temp(
    root: Path, target: Path, matches: list[tuple[int, int, bytes]],
    original_digest: bytes, original_size: int, original_stamp: tuple[int, ...],
    original_mode: int, *, subject: dict, settings: EditSettings, used_fuzzy: bool,
    control=None,
    progress=None,
) -> tuple[str, dict]:
    check_tool_stop(control, EDIT_STOPPED)
    progress = progress or ToolProgress(None)
    progress.report(phase='temp_copy', completed=0, unit='bytes')
    temporary: Path | None = None
    collector = EditFeedback(matches, settings)
    try:
        try:
            if verify_file_target(subject) != target:
                raise ToolException(WRITE_CHANGED)
            path_stamp = require_file_stamp(target.stat(), original_stamp, same_source=False)
            with target.open('rb') as source, tempfile.NamedTemporaryFile(
                mode='wb', prefix='.naiyue-write-', dir=target.parent, delete=False,
            ) as output:
                temporary = Path(output.name)
                check_tool_stop(control, EDIT_STOPPED)
                require_file_stamp(os.fstat(source.fileno()), original_stamp)
                digest = hashlib.sha256()
                output_digest = hashlib.sha256()
                position = 0
                output_size = 0

                def transfer(stop: int, *, copy: bool) -> None:
                    nonlocal position, output_size
                    while position < stop:
                        check_tool_stop(control, EDIT_STOPPED)
                        chunk = source.read(min(_CHUNK_BYTES, stop - position))
                        check_tool_stop(control, EDIT_STOPPED)
                        if not chunk:
                            raise ToolException(WRITE_CHANGED)
                        digest.update(chunk)
                        collector.feed(chunk)
                        position += len(chunk)
                        if copy:
                            output.write(chunk)
                            output_digest.update(chunk)
                            output_size += len(chunk)
                        progress.report(phase='temp_copy', completed=position, unit='bytes')
                        check_tool_stop(control, EDIT_STOPPED)

                for start, end, replacement in matches:
                    check_tool_stop(control, EDIT_STOPPED)
                    transfer(start, copy=True)
                    transfer(end, copy=False)
                    output.write(replacement)
                    check_tool_stop(control, EDIT_STOPPED)
                    output_digest.update(replacement)
                    output_size += len(replacement)
                while chunk := source.read(_CHUNK_BYTES):
                    check_tool_stop(control, EDIT_STOPPED)
                    digest.update(chunk)
                    collector.feed(chunk)
                    output.write(chunk)
                    output_digest.update(chunk)
                    position += len(chunk)
                    output_size += len(chunk)
                    progress.report(phase='temp_copy', completed=position, unit='bytes')
                    check_tool_stop(control, EDIT_STOPPED)
                check_tool_stop(control, EDIT_STOPPED)
                if position != original_size or digest.digest() != original_digest:
                    raise ToolException(WRITE_CHANGED)
                require_file_stamp(os.fstat(source.fileno()), original_stamp)
                if output_digest.digest() == original_digest:
                    raise ToolException(EDIT_NO_CHANGE)
                display_path = target.relative_to(root).as_posix() if target.is_relative_to(root) else target.as_posix()
                progress.report(phase='diff')
                detail, artifact = build_edit_feedback(collector, display_path, used_fuzzy)
                check_tool_stop(control, EDIT_STOPPED)
                content = f'edit replaced {len(matches)} block(s); {output_size} bytes\n{detail}'
                if os.name != 'nt':
                    os.fchmod(output.fileno(), original_mode & 0o777)
                check_tool_stop(control, EDIT_STOPPED)
                progress.report(phase='flush')
                output.flush()
                check_tool_stop(control, EDIT_STOPPED)
                progress.report(phase='fsync')
                os.fsync(output.fileno())
                check_tool_stop(control, EDIT_STOPPED)
            if verify_file_target(subject) != target:
                raise ToolException(WRITE_CHANGED)
            require_file_stamp(target.stat(), path_stamp)
        except (OSError, ValueError, RuntimeError) as error:
            logger.warning('edit_failed phase=prepare error_type=%s', type(error).__name__)
            raise ToolException(EDIT_IO_FAILURE) from error
        progress.report(phase='commit')
        check_tool_stop(control, EDIT_STOPPED)
        _replace_temp(temporary, target, existed=True)
        temporary = None
        progress.report(phase='commit', activity='settling', completed=1, total=1)
        return content, artifact
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                logger.warning('edit_temp_cleanup_failed error_type=%s',
                               type(error).__name__)
        progress.report(phase='cleanup', activity='settling')


def _edit(root: Path, path: str, edits: list[EditReplacement], settings: EditSettings, *, subject: dict, control=None, progress=None):
    check_tool_stop(control, EDIT_STOPPED)
    progress = progress or ToolProgress(None)
    max_bytes = settings.max_input_bytes
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
        target = _resolve_write_target(root, path)
        if not target.is_file():
            raise ValueError('missing_file')
    except (OSError, ValueError, RuntimeError) as error:
        logger.warning('edit_failed phase=read error_type=%s', type(error).__name__)
        raise ToolException(EDIT_UNAVAILABLE) from error
    check_tool_stop(control, EDIT_STOPPED)
    with _reserve_target(target):
        try:
            scan = _scan_target(target, old_parts, control=control, progress=progress)
            plan = prepare_edit_plan(target, old_parts, new_parts, scan, settings, _CHUNK_BYTES, control=control, progress=progress)
        except UnicodeError as error:
            logger.warning('edit_failed phase=decode error_type=%s', type(error).__name__)
            raise ToolException(EDIT_ENCODING) from error
        except (OSError, ValueError, RuntimeError) as error:
            logger.warning('edit_failed phase=scan error_type=%s', type(error).__name__)
            raise ToolException(EDIT_IO_FAILURE) from error
        snapshot = plan['snapshot']
        return _copy_to_temp(root, target, plan['matches'], snapshot['digest'],
            snapshot['size'], snapshot['stamp'], snapshot['mode'], subject=subject,
            settings=settings, used_fuzzy=plan['used_fuzzy'], control=control, progress=progress)


def create_session_edit_tool(*, settings: EditSettings | None = None, progress_settings=None):
    """Edit an existing UTF-8 file in the application-owned session workspace."""
    settings = settings if settings is not None else EditSettings()
    public_schema, execution_schema = build_tool_schemas('EditInput', _EditInput,
        path=(str, Field(min_length=1, max_length=settings.max_path_chars)))

    @tool('edit', args_schema=execution_schema, response_format='content_and_artifact')
    def edit(
        path: str, edits: list[EditReplacement], runtime: ToolRuntime[RuntimeContext],
    ) -> tuple[str, dict]:
        """Replace unique, non-overlapping text in one existing UTF-8 file.

        Every oldText is matched against the original file. Read it first to get
        exact whitespace and newlines; finite Unicode/newline normalization is a
        fallback, never edit distance. Returns firstChangedLine and a bounded diff.
        This tool cannot create a file or missing parent directories.
        """
        context = runtime.context
        root = context.scope.identity.execution_path if isinstance(context, RuntimeContext) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('edit_failed phase=context error_type=MissingWorkspaceContext')
            raise ToolException(EDIT_NO_WORKSPACE)
        target = require_file_target(runtime, path, operation='write')
        progress = ToolProgress(runtime, progress_settings)
        try:
            return _edit(root, str(target), edits, settings,
                         subject=context.authorization.permit['subject'], control=context.scope.control, progress=progress)
        finally:
            progress.flush()

    edit.handle_validation_error = EDIT_INVALID_ARGS
    edit.description += (f' Combined oldText/newText input limit: '
                         f'{settings.max_input_bytes} bytes of UTF-8; original file size is not limited by this budget.')
    return ToolSpec(edit, public_schema, execution_mode='sequential', side_effects='may_mutate', permissions=FilePermission('path', 'write', EDIT_UNAVAILABLE, EDIT_NO_WORKSPACE))
