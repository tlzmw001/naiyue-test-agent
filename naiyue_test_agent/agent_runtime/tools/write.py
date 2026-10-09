"""Whole-file UTF-8 writes confined to a trusted session workspace."""

import logging
import os
import stat
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Iterator

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field

from .read import _has_disallowed_hidden_part


logger = logging.getLogger(__name__)
_MAX_WRITE_ENV = 'NAIYUE_WRITE_MAX_BYTES'
_DEFAULT_MAX_WRITE_BYTES = 1024 * 1024
_active_targets: set[str] = set()
_active_targets_lock = Lock()

WRITE_UNAVAILABLE = ('write cannot access this path: it may be outside the workspace, '
                     'missing a parent directory, or blocked by workspace policy. '
                     'Choose an allowed file in an existing workspace directory.')
WRITE_LIMIT = ('write content exceeds the configured size limit. Shorten the content '
               'or split the work into smaller files.')
WRITE_ENCODING = ('write content must be valid UTF-8 text. Remove invalid Unicode '
                  'characters before trying again.')
WRITE_BUSY = ('write target is busy with another write. Read the file again, confirm '
              'the intended content, then retry.')
WRITE_CHANGED = ('The file changed since it was read. Read the file again, confirm '
                 'the intended edit, then retry.')
WRITE_IO_FAILURE = ('write could not prepare the file due to an I/O error. Check '
                    'workspace permissions or free space before trying again.')
WRITE_NO_WORKSPACE = ('write has no workspace for this session. Select a workspace '
                      'before writing files.')
WRITE_INVALID_ARGS = ('Invalid write arguments. Provide path as a non-empty string '
                      'and content as a string; remove unknown fields.')
WRITE_TOOL_FAILURE = ('write failed unexpectedly. Check the file before trying again.')
SAFE_WRITE_ERRORS = frozenset((WRITE_UNAVAILABLE, WRITE_LIMIT, WRITE_ENCODING,
                               WRITE_BUSY, WRITE_CHANGED, WRITE_IO_FAILURE, WRITE_NO_WORKSPACE,
                               WRITE_TOOL_FAILURE))


class WriteOutcomeUnknown(RuntimeError):
    """The commit boundary did not confirm whether a file was replaced."""


class WriteInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(min_length=1, max_length=4096)
    content: str


class SessionWriteInput(WriteInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)

    runtime: ToolRuntime[dict, dict]


def _write_limit() -> int:
    raw = os.environ.get(_MAX_WRITE_ENV, str(_DEFAULT_MAX_WRITE_BYTES))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError('Invalid write size limit') from error
    if value <= 0:
        raise ValueError('Invalid write size limit')
    return value


def _target_in_workspace(root: Path, path: str) -> Path:
    requested = Path(path)
    if '..' in requested.parts:
        raise ValueError('traversal')
    try:
        relative = requested.relative_to(root) if requested.is_absolute() else requested
    except ValueError as error:
        raise ValueError('outside_workspace') from error
    if not relative.parts or _has_disallowed_hidden_part(
        relative.parts, inside_workspace=True,
    ):
        raise ValueError('disallowed_path')
    if relative.suffix.lower() in ('.pem', '.key'):
        raise ValueError('sensitive_file')

    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError('symlink_path')
    parent = candidate.parent.resolve(strict=True)
    if not parent.is_dir() or not parent.is_relative_to(root):
        raise ValueError('outside_workspace')
    if candidate.exists() and not candidate.is_file():
        raise ValueError('not_regular_file')
    target = candidate.resolve(strict=False)
    if not target.is_relative_to(root):
        raise ValueError('outside_workspace')
    return target


@contextmanager
def _reserve_target(target: Path) -> Iterator[None]:
    lock_key = unicodedata.normalize('NFD', str(target).casefold())
    with _active_targets_lock:
        if lock_key in _active_targets:
            logger.warning('write_failed phase=conflict error_type=TargetBusy')
            raise ToolException(WRITE_BUSY)
        _active_targets.add(lock_key)
    try:
        yield
    finally:
        with _active_targets_lock:
            _active_targets.remove(lock_key)


def _replace_temp(temporary: Path, target: Path) -> None:
    try:
        os.replace(temporary, target)
    except OSError as error:
        logger.error('write_commit_unknown error_type=%s', type(error).__name__)
        raise WriteOutcomeUnknown('File write outcome is unknown; inspect the file '
                                  'before another write.') from error


def _write(
    root: Path, path: str, content: str, max_bytes: int, *,
    expected_content: bytes | None = None,
) -> str:
    if len(content) > max_bytes:
        logger.warning('write_failed phase=limit error_type=SizeLimit')
        raise ToolException(WRITE_LIMIT)
    try:
        encoded = content.encode('utf-8')
    except UnicodeError as error:
        logger.warning('write_failed phase=encode error_type=%s', type(error).__name__)
        raise ToolException(WRITE_ENCODING) from error
    if len(encoded) > max_bytes:
        logger.warning('write_failed phase=limit error_type=SizeLimit')
        raise ToolException(WRITE_LIMIT)
    try:
        target = _target_in_workspace(root, path)
    except (OSError, ValueError, RuntimeError) as error:
        logger.warning('write_failed phase=path error_type=%s', type(error).__name__)
        raise ToolException(WRITE_UNAVAILABLE) from error

    with _reserve_target(target):
        temporary: Path | None = None
        try:
            try:
                if _target_in_workspace(root, path) != target:
                    raise ValueError('target_changed')
                if expected_content is not None:
                    try:
                        with target.open('rb') as source:
                            current = source.read(len(expected_content) + 1)
                    except FileNotFoundError:
                        current = None
                    if current != expected_content:
                        raise ToolException(WRITE_CHANGED)
                existed = target.exists()
                existing_mode = (stat.S_IMODE(target.stat().st_mode) & 0o777
                                 if existed else None)
                with tempfile.NamedTemporaryFile(
                    mode='wb', prefix='.naiyue-write-', dir=target.parent,
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    output.write(encoded)
                    if existing_mode is not None:
                        os.fchmod(output.fileno(), existing_mode)
                    output.flush()
                    os.fsync(output.fileno())
                if _target_in_workspace(root, path) != target:
                    raise ValueError('target_changed')
                if expected_content is not None:
                    try:
                        with target.open('rb') as source:
                            current = source.read(len(expected_content) + 1)
                    except FileNotFoundError:
                        current = None
                    if current != expected_content:
                        raise ToolException(WRITE_CHANGED)
            except (OSError, ValueError, RuntimeError) as error:
                logger.warning('write_failed phase=prepare error_type=%s', type(error).__name__)
                raise ToolException(WRITE_IO_FAILURE) from error

            _replace_temp(temporary, target)
            temporary = None
            return f'write {"replaced" if existed else "created"} {len(encoded)} bytes'
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    logger.warning('write_temp_cleanup_failed error_type=%s',
                                   type(error).__name__)


def _safe_write_error(error: ToolException) -> str:
    message = error.args[0] if error.args else None
    return message if isinstance(message, str) and message in SAFE_WRITE_ERRORS else WRITE_TOOL_FAILURE


def create_session_write_tool():
    """Write complete UTF-8 files inside the application-owned session workspace."""
    max_bytes = _write_limit()

    @tool('write', args_schema=SessionWriteInput)
    def write(path: str, content: str, runtime: ToolRuntime[dict, dict]) -> str:
        """Create or completely overwrite one workspace UTF-8 file.

        The parent directory must already exist. This tool cannot make partial edits.
        """
        context = runtime.context
        root = context.get('workspace_root') if isinstance(context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('write_failed phase=context error_type=MissingWorkspaceContext')
            raise ToolException(WRITE_NO_WORKSPACE)
        try:
            return _write(root, path, content, max_bytes)
        except WriteOutcomeUnknown:
            logger.error('write_outcome_unknown', extra={'write_event': {
                'event': 'write_outcome_unknown',
                'run_id': context.get('run_id'),
                'tool_call_id': runtime.tool_call_id,
            }})
            raise

    write.handle_validation_error = WRITE_INVALID_ARGS
    write.handle_tool_error = _safe_write_error
    write.metadata = {'naiyue_execution_mode': 'sequential'}
    return write
