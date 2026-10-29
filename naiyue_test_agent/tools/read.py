"""A bounded UTF-8 reader for an explicitly chosen local workspace."""

from naiyue_test_agent.contracts.run_context import RuntimeContext
import codecs
from contextlib import nullcontext
import logging
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field

from .schemas import build_tool_schemas
from .settings import ReadSettings
from .registry import ToolSpec

from .permissions import FilePermission, require_file_target
from .images import read_image
from .control import check_tool_stop
from .progress import ToolProgress


logger = logging.getLogger(__name__)
READ_UNAVAILABLE = ('read cannot access this path: it may be missing, not a regular '
                    'file, or blocked by workspace policy. Check the path or choose an '
                    'allowed file.')
READ_IO_FAILURE = ('read could not complete the file read due to an I/O error. '
                   'Check file availability or choose another file.')
READ_MISSING = 'read target is missing. Check the path and retry with an existing file.'
READ_NOT_FILE = 'read target is not a regular file. Choose a file rather than a directory.'
READ_PERMISSION_DENIED = 'read lacks filesystem permission for this file. Check access permissions or choose another file.'
READ_NO_WORKSPACE = ('read has no workspace for this session. Select a workspace '
                     'before reading files.')
INVALID_ARGS = ('Invalid read arguments. Provide path as a non-empty string; '
                'offset as an integer >= 1 and limit as an integer from 1 to {max_limit} '
                'when present; remove unknown fields.')
INVALID_ENCODING = ('read only supports UTF-8 text; this file is not valid UTF-8. '
                    'Choose a UTF-8 text file.')
NON_TEXT_BYTES = ('read only supports text; this file contains non-text control '
                  'bytes. Choose a text file.')
READ_STOPPED = 'read stopped by the user; the read is incomplete and no file content was returned.'


class _ReadInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(min_length=1)
    offset: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1)


def resolve_workspace_root(workspace_root: str | Path) -> Path:
    """Validate and canonicalize a trusted application-provided root."""
    try:
        root = Path(workspace_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('workspace must be an existing directory')
    except (OSError, RuntimeError) as error:
        raise ValueError('workspace must be an existing directory') from error
    return root




def _decode_read_text(content: bytes, *, partial=False) -> str:
    text = codecs.getincrementaldecoder('utf-8')().decode(content, final=not partial)
    if any((byte < 32 and byte not in (9, 10, 13)) or byte == 127 for byte in content):
        raise ValueError('non_text_content')
    return text


def _read(target: Path, offset: int | None, limit: int | None, *, settings: ReadSettings, control=None, progress=None, source=None) -> str | list[dict]:
    check_tool_stop(control, READ_STOPPED)
    progress = progress or ToolProgress(None)
    try:
        with (target.open('rb') if source is None else nullcontext(source)) as source:
            image = read_image(source, offset, limit, settings=settings.image, control=control, progress=progress)
            check_tool_stop(control, READ_STOPPED)
            if image is not None:
                return image
            source.seek(0)
            if offset is None and limit is None:
                progress.report(phase='read', completed=0, unit='bytes')
                content = source.read(settings.max_output_bytes + 1)
                progress.report(phase='read', completed=len(content), unit='bytes')
                check_tool_stop(control, READ_STOPPED)
                line_count = content.count(b'\n') + int(
                    bool(content) and not content.endswith(b'\n')
                )
                if len(content) <= settings.max_output_bytes and line_count <= settings.default_limit:
                    result = _decode_read_text(content)
                    check_tool_stop(control, READ_STOPPED)
                    return result
                source.seek(0)

            first_line = offset or 1
            page_limit = limit or settings.default_limit
            line_number = 0
            skipped_bytes = 0
            while line_number < first_line - 1:
                check_tool_stop(control, READ_STOPPED)
                chunk = source.readline(settings.max_output_bytes + 1)
                skipped_bytes += len(chunk)
                progress.report(phase='skip_lines', completed=skipped_bytes, unit='bytes')
                check_tool_stop(control, READ_STOPPED)
                if not chunk:
                    break
                while chunk and not chunk.endswith(b'\n'):
                    check_tool_stop(control, READ_STOPPED)
                    chunk = source.readline(settings.max_output_bytes + 1)
                    skipped_bytes += len(chunk)
                    progress.report(phase='skip_lines', completed=skipped_bytes, unit='bytes')
                    check_tool_stop(control, READ_STOPPED)
                line_number += 1

            lines: list[str] = []
            page_bytes = 0
            has_more = False
            stopped_by = None
            processed_bytes = 0
            if line_number == first_line - 1:
                progress.report(phase='read_page', completed=0, unit='bytes')
                while len(lines) < page_limit:
                    check_tool_stop(control, READ_STOPPED)
                    chunk = source.readline(settings.max_output_bytes + 1)
                    processed_bytes += len(chunk)
                    progress.report(phase='read_page', completed=processed_bytes, unit='bytes')
                    check_tool_stop(control, READ_STOPPED)
                    if not chunk:
                        break
                    if len(chunk) > settings.max_output_bytes:
                        if lines:
                            has_more = True
                            stopped_by = f'line {line_number + 1} exceeding {settings.max_output_bytes} bytes'
                            break
                        prefix = _decode_read_text(chunk[:settings.max_output_bytes], partial=True)
                        check_tool_stop(control, READ_STOPPED)
                        return (prefix + ('\n' if prefix else '')
                            + f'[read: line {first_line} partial; returned_bytes={len(prefix.encode("utf-8"))}; '
                              f'byte_limit={settings.max_output_bytes}; next_offset=unavailable; '
                              'line remainder omitted. offset/limit cannot resume within this line; '
                              'use a shell to inspect the remainder.]')
                    if page_bytes + len(chunk) > settings.max_output_bytes:
                        has_more = True
                        stopped_by = f'{settings.max_output_bytes} byte page limit'
                        break
                    lines.append(_decode_read_text(chunk))
                    page_bytes += len(chunk)
                    line_number += 1
                if len(lines) == page_limit:
                    check_tool_stop(control, READ_STOPPED)
                    has_more = bool(source.read(1))
                    check_tool_stop(control, READ_STOPPED)
                    if has_more:
                        stopped_by = f'{page_limit} line page limit'

        if not lines:
            result = f'[read: no lines from offset={first_line}; next_offset=none]'
        else:
            next_offset = str(line_number + 1) if has_more else 'none'
            result = (''.join(lines) + ('' if lines[-1].endswith('\n') else '\n')
                      + f'[read: lines {first_line}-{line_number}; '
                        f'next_offset={next_offset}]')
            if has_more:
                result += (f'\n[read: stopped by {stopped_by}; continue with the same path, '
                           f'offset={next_offset}, limit={page_limit}]')
        check_tool_stop(control, READ_STOPPED)
        return result
    except UnicodeError as error:
        logger.warning('read_failed error_type=%s', type(error).__name__)
        raise ToolException(INVALID_ENCODING) from error
    except ValueError as error:
        message = NON_TEXT_BYTES if str(error) == 'non_text_content' else READ_UNAVAILABLE
        logger.warning('read_failed error_type=%s', type(error).__name__)
        raise ToolException(message) from error
    except OSError as error:
        logger.warning('read_failed error_type=%s', type(error).__name__)
        message = (READ_MISSING if isinstance(error, FileNotFoundError) else
                   READ_NOT_FILE if isinstance(error, IsADirectoryError) else
                   READ_PERMISSION_DENIED if isinstance(error, PermissionError) else READ_IO_FAILURE)
        raise ToolException(message) from error


def create_session_read_tool(*, settings: ReadSettings | None = None, progress_settings=None):
    """Create a read tool whose root comes from the trusted run context."""

    settings = settings if settings is not None else ReadSettings()
    public_schema, execution_schema = build_tool_schemas('ReadInput', _ReadInput,
        path=(str, Field(min_length=1, max_length=settings.max_path_chars)),
        limit=(int | None, Field(default=None, ge=1, le=settings.max_limit)))

    @tool('read', args_schema=execution_schema)
    async def read(path: str, runtime: ToolRuntime[RuntimeContext],
                  offset: int | None = None, limit: int | None = None) -> str | list[dict]:
        """Read UTF-8 text with 1-based pagination, or an image without offset/limit."""
        context = runtime.context
        from naiyue_test_agent.execution.file_calls import file_call
        require_file_target(runtime, path, operation='read')
        progress = ToolProgress(runtime, progress_settings)
        try:
            return await file_call(context.scope.capabilities.files.read,context.authorization.permit['subject'], offset, limit,
                settings=settings, control=context.scope.control, progress=progress)
        finally:
            progress.flush()

    read.handle_validation_error = INVALID_ARGS.format(max_limit=settings.max_limit)
    read.description += (f' Default page: {settings.default_limit} lines, at most '
                         f'{settings.max_output_bytes} bytes; limit may be up to {settings.max_limit}. '
                         'Follow next_offset for further complete lines. An oversized first line returns '
                         'a UTF-8 prefix marked partial; offset/limit cannot resume its omitted remainder.')
    return ToolSpec(read, public_schema, side_effects='read_only', permissions=FilePermission('path', 'read', READ_UNAVAILABLE, READ_NO_WORKSPACE))
