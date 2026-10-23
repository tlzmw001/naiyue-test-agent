"""Whole-file UTF-8 writes to a permission-approved real target."""

from naiyue_test_agent.contracts.run_context import RuntimeContext
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

from .permissions import FilePermission, require_file_target
from .paths import verify_file_target, permission_path
from .schemas import build_tool_schemas
from .settings import WriteSettings
from .registry import ToolSpec
from .control import check_tool_stop
from .progress import ToolProgress
from .file_change import write_feedback, read_feedback_allowed


logger = logging.getLogger(__name__)
_active_targets: set[str] = set()
_active_targets_lock = Lock()

WRITE_UNAVAILABLE = ('write cannot use this path. Check the file type, links and '
                     'access permissions, then choose an allowed file target.')
WRITE_PARENT_FAILURE = ('write could not create or verify its parent directories. '
                        'The file was not written; parent directories may already have been created. '
                        'Check the path and filesystem permissions before retrying.')
WRITE_PARENT_CHANGED = ('write target changed while preparing parent directories. '
                        'The file was not written; parent directories may already have been created. '
                        'Reissue the tool call to review the current target.')
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
WRITE_STOPPED = ('write stopped by the user before file commit; the target file was not written. '
                 'Some parent directories may already have been created.')


class _WriteInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(min_length=1)
    content: str


def _resolve_write_target(root: Path, path: str, *, require_parent=True) -> Path:
    """IO-level identity check; authorization already selected the real target."""
    candidate = Path(path) if Path(path).is_absolute() else root / path
    target = candidate.resolve(strict=False)
    if (candidate != target or require_parent and not target.parent.is_dir()
            or target.exists() and not target.is_file()):
        raise ValueError('write_target_changed_or_unavailable')
    return target


@contextmanager
def _reserve_target(target: Path) -> Iterator[None]:
    lock_key = unicodedata.normalize('NFD', str(permission_path(target)).casefold())
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


def _windows_replace_file(temporary: Path, target: Path) -> None:
    """Replace content while Windows preserves the target DACL; never ignore merge errors."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ReplaceFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    kernel.ReplaceFileW.restype = wintypes.BOOL
    if not kernel.ReplaceFileW(str(target), str(temporary), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def _replace_temp(temporary: Path, target: Path, *, existed: bool) -> None:
    try:
        if os.name == 'nt':
            if existed:
                if getattr(target.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_READONLY:
                    raise ToolException('File is read-only; change its attribute explicitly before retrying.')
                _windows_replace_file(temporary, target)
            else:
                # Windows rename refuses a target that appeared after preparation.
                os.rename(temporary, target)
        else:
            os.replace(temporary, target)
    except OSError as error:
        logger.error('write_commit_unknown error_type=%s', type(error).__name__)
        raise ToolException('File write outcome is unknown; inspect the file '
                            'before another write.') from error


def _write(
    root: Path, path: str, content: str, max_bytes: int, *,
    subject: dict,
    expected_content: bytes | None = None,
    control=None,
    progress=None,
    feedback_artifact=None, feedback_settings=None, feedback_read_allowed=False, feedback_path=None,
) -> str:
    check_tool_stop(control, WRITE_STOPPED)
    progress = progress or ToolProgress(None)
    progress.report(phase='write_prepare')
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
        target = _resolve_write_target(root, path, require_parent=False)
    except (OSError, ValueError, RuntimeError) as error:
        logger.warning('write_failed phase=path error_type=%s', type(error).__name__)
        raise ToolException(WRITE_UNAVAILABLE) from error

    check_tool_stop(control, WRITE_STOPPED)
    with _reserve_target(target):
        temporary: Path | None = None
        try:
            check_tool_stop(control, WRITE_STOPPED)
            try:
                if verify_file_target(subject) != target:
                    raise ToolException('File target does not match the reviewed target')
                missing = []
                parent = target.parent
                while not parent.exists():
                    if parent == parent.parent:
                        raise ValueError('No existing directory anchor')
                    missing.append(parent)
                    parent = parent.parent
                if not parent.is_dir():
                    raise NotADirectoryError('Parent is not a directory')
            except ToolException as error:
                logger.warning('write_failed phase=parents error_type=%s', type(error).__name__)
                raise ToolException(WRITE_PARENT_CHANGED) from error
            except (OSError, ValueError, RuntimeError) as error:
                logger.warning('write_failed phase=parents error_type=%s', type(error).__name__)
                raise ToolException(WRITE_PARENT_FAILURE) from error
            for index, parent in enumerate(reversed(missing)):
                check_tool_stop(control, WRITE_STOPPED)
                try:
                    verify_file_target(subject)
                    parent.mkdir(exist_ok=True)
                    verify_file_target(subject)
                except ToolException as error:
                    logger.warning('write_failed phase=parents error_type=%s', type(error).__name__)
                    raise ToolException(WRITE_PARENT_CHANGED) from error
                except (OSError, ValueError, RuntimeError) as error:
                    logger.warning('write_failed phase=parents error_type=%s', type(error).__name__)
                    raise ToolException(WRITE_PARENT_FAILURE) from error
                check_tool_stop(control, WRITE_STOPPED)
                progress.report(phase='create_parents', completed=index + 1, total=len(missing), unit='entries')
            check_tool_stop(control, WRITE_STOPPED)
            try:
                verify_file_target(subject)
                if _resolve_write_target(root, path) != target:
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
                feedback_stat = target.stat() if existed else None
                if feedback_artifact is not None:
                    feedback_artifact.update(write_feedback(target, content, feedback_settings,
                        read_allowed=feedback_read_allowed, display_path=feedback_path))
                check_tool_stop(control, WRITE_STOPPED)
                with tempfile.NamedTemporaryFile(
                    mode='wb', prefix='.naiyue-write-', dir=target.parent,
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    check_tool_stop(control, WRITE_STOPPED)
                    progress.report(phase='temp_write', completed=0, total=len(encoded), unit='bytes')
                    output.write(encoded)
                    progress.report(phase='temp_write', completed=len(encoded), total=len(encoded), unit='bytes')
                    check_tool_stop(control, WRITE_STOPPED)
                    if existing_mode is not None and os.name != 'nt':
                        os.fchmod(output.fileno(), existing_mode)
                    check_tool_stop(control, WRITE_STOPPED)
                    progress.report(phase='flush')
                    output.flush()
                    check_tool_stop(control, WRITE_STOPPED)
                    progress.report(phase='fsync')
                    os.fsync(output.fileno())
                    check_tool_stop(control, WRITE_STOPPED)
                verify_file_target(subject)
                if _resolve_write_target(root, path) != target:
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

            if feedback_artifact is not None:
                current_stat = target.stat() if target.exists() else None
                fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
                if (current_stat is None) != (feedback_stat is None) or current_stat is not None and any(
                        getattr(current_stat, field) != getattr(feedback_stat, field) for field in fields):
                    raise ToolException(WRITE_CHANGED)
            progress.report(phase='commit')
            check_tool_stop(control, WRITE_STOPPED)
            _replace_temp(temporary, target, existed=existed)
            temporary = None
            progress.report(phase='commit', activity='settling', completed=1, total=1)
            return f'write {"replaced" if existed else "created"} {len(encoded)} bytes'
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    logger.warning('write_temp_cleanup_failed error_type=%s',
                                   type(error).__name__)
            progress.report(phase='cleanup', activity='settling')


def create_session_write_tool(*, settings: WriteSettings | None = None, progress_settings=None):
    """Write complete UTF-8 files to session-authorized targets."""
    settings = settings if settings is not None else WriteSettings()
    public_schema, execution_schema = build_tool_schemas('WriteInput', _WriteInput,
        path=(str, Field(min_length=1, max_length=settings.max_path_chars)))

    @tool('write', args_schema=execution_schema, response_format='content_and_artifact')
    def write(path: str, content: str, runtime: ToolRuntime[RuntimeContext]) -> tuple:
        """Create or completely overwrite one authorized UTF-8 file.

        Missing parent directories are created after authorization. This tool cannot make partial edits.
        """
        context = runtime.context
        root = context.scope.identity.execution_path if isinstance(context, RuntimeContext) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('write_failed phase=context error_type=MissingWorkspaceContext')
            raise ToolException(WRITE_NO_WORKSPACE)
        check_tool_stop(context.scope.control, WRITE_STOPPED)
        target = require_file_target(runtime, path, operation='write')
        progress = ToolProgress(runtime, progress_settings)
        try:
            artifact = {}
            result = _write(root, str(target), content, settings.max_input_bytes,
                          subject=context.authorization.permit['subject'], control=context.scope.control, progress=progress,
                          feedback_artifact=artifact, feedback_settings=settings,
                          feedback_read_allowed=read_feedback_allowed(runtime, path), feedback_path=path)
            return result, artifact
        finally:
            progress.flush()

    write.handle_validation_error = WRITE_INVALID_ARGS
    write.description += f' Content input limit: {settings.max_input_bytes} bytes of UTF-8.'
    return ToolSpec(write, public_schema, execution_mode='sequential', side_effects='may_mutate', permissions=FilePermission('path', 'write', WRITE_UNAVAILABLE, WRITE_NO_WORKSPACE))
