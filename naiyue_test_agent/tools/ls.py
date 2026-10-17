"""Bounded directory names for one trusted local workspace."""

import json
import logging
import os
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field

from .permissions import FilePermission, require_file_target, candidate_access, skipped_scope_message
from .schemas import build_tool_schemas
from .settings import LsSettings
from .registry import ToolSpec
from .progress import ToolProgress


logger = logging.getLogger(__name__)
LS_UNAVAILABLE = ('ls cannot list this path: it may be missing, not a directory, or '
                  'blocked by workspace policy. Check the path or choose an allowed directory.')
LS_IO_FAILURE = ('ls could not complete the directory listing due to an I/O error. '
                 'Check directory availability or choose another directory.')
LS_MISSING = 'ls target is missing. Check the path and retry with an existing directory.'
LS_NOT_DIRECTORY = 'ls target is not a directory. Choose a directory rather than a file.'
LS_PERMISSION_DENIED = 'ls lacks filesystem permission to list this directory. Check access permissions or choose another directory.'
LS_STOPPED = 'ls was stopped by the user before any permitted names could be returned. No complete directory listing is available.'
LS_NO_WORKSPACE = ('ls has no workspace for this session. Select a workspace before '
                   'listing directories.')
LS_INVALID_ARGS = ('Invalid ls arguments. Provide path as a non-empty string when present; '
                   'limit as an integer from 1 to {max_limit} when present; remove unknown fields.')


class _LsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(default='.', min_length=1)
    limit: int = Field(ge=1)


def _list_directory(target: Path, limit: int, runtime, *, settings: LsSettings, progress=None) -> str:
    control = runtime.context.get('_run_control')
    progress = progress or ToolProgress(None)
    try:
        if control is not None and control.requested:
            raise ToolException(LS_STOPPED)
        entries = []
        progress.report(phase='enumerate', completed=0, unit='entries')
        with os.scandir(target) as scan:
            for entry in scan:
                if control is not None and control.requested:
                    raise ToolException(LS_STOPPED)
                entries.append(entry)
                progress.report(phase='enumerate', completed=len(entries), unit='entries')
        entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
        lines: list[str] = []
        byte_count = 0
        truncated_by = None
        skipped = []
        incomplete = False
        stopped = False
        for inspected, entry in enumerate(entries):
            progress.report(phase='inspect_entries', completed=inspected, total=len(entries), unit='entries')
            if control is not None and control.requested:
                stopped = True
                break
            try:
                allowed, reason = candidate_access(runtime, Path(entry.path), operation='list')
                if not allowed:
                    skipped.append(f'{entry.name}: {reason}')
                    continue
                is_directory = entry.is_dir(follow_symlinks=True)
                entry.stat(follow_symlinks=True)
            except OSError as error:
                logger.warning('ls_entry_stat_failed error_type=%s', type(error).__name__)
                reason = ('disappeared' if isinstance(error, FileNotFoundError) else
                          'permission_denied' if isinstance(error, PermissionError) else 'io_error')
                skipped.append(f'{entry.name}: {reason}')
                incomplete = True
                continue
            if control is not None and control.requested:
                stopped = True
                break
            if len(lines) >= limit:
                truncated_by = f'{limit} entries'
                break
            name = json.dumps(entry.name, ensure_ascii=False)[1:-1]
            line = name + ('/' if is_directory else '')
            addition = len(line.encode('utf-8')) + int(bool(lines))
            if byte_count + addition > settings.max_output_bytes:
                budget = (f'{settings.max_output_bytes // 1024} KiB'
                          if settings.max_output_bytes % 1024 == 0
                          else f'{settings.max_output_bytes} bytes')
                truncated_by = f'{budget} output'
                break
            lines.append(line)
            byte_count += addition

        stopped = stopped or control is not None and control.requested
        progress.report(phase='list_results', completed=len(lines), unit='entries')
        if stopped and not lines:
            raise ToolException(LS_STOPPED)
        if not lines and truncated_by is None and not skipped:
            return '(empty directory)'
        body = '\n'.join(lines)
        if truncated_by is not None:
            body += f'\n[ls: truncated at {truncated_by}; narrow the directory or limit]'
        if stopped:
            body += ('\n' if body else '') + '[ls: incomplete; stopped by the user]'
        elif incomplete:
            body += ('\n' if body else '') + '[ls: incomplete; some entries could not be inspected]'
        body += skipped_scope_message('ls', skipped, settings.max_output_bytes)
        return body
    except ValueError as error:
        message = LS_UNAVAILABLE
        logger.warning('ls_failed error_type=%s', type(error).__name__)
        raise ToolException(message) from error
    except OSError as error:
        logger.warning('ls_failed error_type=%s', type(error).__name__)
        message = (LS_MISSING if isinstance(error, FileNotFoundError) else
                   LS_NOT_DIRECTORY if isinstance(error, NotADirectoryError) else
                   LS_PERMISSION_DENIED if isinstance(error, PermissionError) else LS_IO_FAILURE)
        raise ToolException(message) from error


def create_session_ls_tool(*, settings: LsSettings | None = None, progress_settings=None):
    """Create an ls tool whose workspace root comes from the trusted run context."""

    settings = settings if settings is not None else LsSettings()
    public_schema, execution_schema = build_tool_schemas('LsInput', _LsInput,
        path=(str, Field(default='.', min_length=1, max_length=settings.max_path_chars)),
        limit=(int, Field(default=settings.default_limit, ge=1, le=settings.max_limit)))

    @tool('ls', args_schema=execution_schema)
    def list_directory(runtime: ToolRuntime[dict, dict], path: str = '.',
                       limit: int = settings.default_limit) -> str:
        """List permitted names and report any skipped targets."""
        context = runtime.context
        root = context.get('workspace_root') if isinstance(context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('ls_failed error_type=MissingWorkspaceContext')
            raise ToolException(LS_NO_WORKSPACE)
        target = require_file_target(runtime, path, operation='list')
        progress = ToolProgress(runtime, progress_settings)
        try:
            return _list_directory(target, limit, runtime, settings=settings, progress=progress)
        finally:
            progress.flush()

    list_directory.handle_validation_error = LS_INVALID_ARGS.format(max_limit=settings.max_limit)
    list_directory.description += (f' Default: {settings.default_limit} entries, at most '
                                   f'{settings.max_output_bytes} bytes; limit may be up to {settings.max_limit}.')
    return ToolSpec(list_directory, public_schema, side_effects='read_only', permissions=FilePermission('path', 'list', LS_UNAVAILABLE, LS_NO_WORKSPACE))
