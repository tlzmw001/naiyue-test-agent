"""Native fd discovery with bounded names and permission checks before counting."""

import asyncio
import logging
import os
from pathlib import Path
import sys

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.resources import resolve_tool_binary

from .paths import prepare_file_subject, verify_file_target
from .permissions import FilePermission, require_file_target, candidate_access
from .processes import HostProcessRunner, run_async_sync
from .registry import ToolSpec
from .schemas import ExecutionTimeout, build_tool_schemas
from .search_protocol import (RecordBuffer, DiagnosticTail, SkippedTargets, SearchStopped,
                              check_search, inside_git, safe_text, stream_records)
from .settings import FindSettings
from .progress import ToolProgress


logger = logging.getLogger(__name__)
FIND_UNAVAILABLE = ('find cannot search this path: it may be missing, not a directory, '
                    'or blocked by workspace policy. Check the path or choose an allowed directory.')
FIND_NO_FD = 'find requires fd, which is unavailable. Install fd and retry.'
FIND_NO_WORKSPACE = 'find has no workspace for this session. Select a workspace first.'
FIND_INVALID_ARGS = ('Invalid find arguments. Provide pattern as a non-empty glob string; '
                     'path as a non-empty directory string; limit as an integer from '
                     '1 to {max_limit}; optional timeout as a nonnegative integer in seconds '
                     '(omit, null, or 0 for no time limit); remove unknown fields.')


class _FindInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    pattern: str = Field(min_length=1)
    path: str = Field(default='.', min_length=1)
    limit: int = Field(ge=1)
    timeout: ExecutionTimeout = None


def _fd_arguments(binary, pattern, target):
    args = [binary, '--glob', '--color=never', '--hidden', '--print0', '--absolute-path']
    if not inside_git(target):
        args.append('--no-require-git')
    if '/' in pattern:
        args.append('--full-path')
        if not pattern.startswith(('/', '**/')) and pattern != '**':
            pattern = '**/' + pattern
        if sys.platform == 'win32':
            pattern = pattern.replace('/', r'[/\\]')
    return [*args, '--', pattern, str(target)]


async def _find_files(target, pattern, limit, runtime, *, settings, runner, progress=None, timeout=None):
    progress = progress or ToolProgress(None)
    if not pattern.strip() or '\x00' in pattern:
        raise ToolException('find needs a valid non-empty glob pattern such as *.py or src/**/*.py.')
    binary = resolve_tool_binary('fd')
    if binary is None:
        raise ToolException(FIND_NO_FD)
    control = runtime.context.get('_run_control')
    deadline = asyncio.get_running_loop().time() + timeout if timeout else None
    parser = RecordBuffer(b'\0', settings.max_record_bytes)
    diagnostics = DiagnosticTail(settings.max_diagnostic_bytes)
    skipped = SkippedTargets(settings.max_output_bytes)
    names, size, incomplete = [], 0, None
    progress.report(phase='find_results', completed=0, unit='entries')

    async def candidate(record):
        nonlocal size, incomplete
        if record is None:
            incomplete = 'a path record exceeded the configured path budget'
            return
        if not record:
            raise ValueError('invalid_record')
        path = Path(os.fsdecode(record))
        if not path.is_absolute():
            path = target / path
        try:
            relative = path.relative_to(target).as_posix().rstrip('/')
        except ValueError as error:
            raise ValueError('candidate_outside_root') from error
        if not relative:
            return
        check_search(deadline, control)
        try:
            subject = prepare_file_subject(str(path), target, 'search')
            allowed, reason = candidate_access(runtime, path, operation='list')
            if not allowed:
                skipped.add(f'{relative}: {reason}')
                return
            real = verify_file_target(subject)
            display = safe_text(relative) + ('/' if real.is_dir() else '')
        except ToolException:
            skipped.add(f'{relative}: unavailable_target')
            incomplete = 'some targets changed or became unavailable'
            return
        if len(names) >= limit:
            raise SearchStopped('truncated at result limit; only the collected subset is sorted')
        addition = len(display.encode()) + int(bool(names))
        if size + addition > settings.max_output_bytes:
            raise SearchStopped('truncated at output byte budget; only the collected subset is sorted')
        names.append((display, subject, path))
        size += addition
        progress.report(phase='find_results', completed=len(names), unit='entries')

    try:
        result = await stream_records(runner, _fd_arguments(binary, pattern, target), target,
            deadline, control, parser, candidate, diagnostics)
        if result.returncode != 0:
            logger.warning('find_native_failed returncode=%s', result.returncode)
            detail = bytes(diagnostics.buffer).lower()
            reason = ('invalid glob pattern' if b'error parsing glob' in detail or b'invalid glob' in detail
                      else 'native discovery failed')
            raise ToolException(f'find could not complete: {reason}. Check the pattern and directory.')
    except SearchStopped as error:
        incomplete = str(error)
    except ValueError as error:
        logger.warning('find_protocol_failed error_type=%s', type(error).__name__)
        raise ToolException('find received incomplete or invalid native search records. No complete result is available.') from error
    except OSError as error:
        logger.warning('find_io_failed error_type=%s', type(error).__name__)
        raise ToolException('find could not start or list targets due to an I/O error. Check fd and directory access.') from error

    output = []
    progress.report(phase='verify_results', completed=0, unit='entries')
    for display, subject, path in names:
        try:
            verify_file_target(subject)
            allowed, reason = candidate_access(runtime, path, operation='list')
        except ToolException:
            allowed, reason = False, 'unavailable_target'
        if allowed:
            output.append(display)
            progress.report(phase='verify_results', completed=len(output), unit='entries')
        else:
            skipped.add(f'{display}: {reason}')
            incomplete = incomplete or 'some targets changed or became unavailable'
    output.sort(key=lambda name: (name.rstrip('/').casefold(), name.rstrip('/')))
    if incomplete and not output:
        raise ToolException(f'find could not complete: {incomplete}. No complete result is available.'
                            + skipped.render('find'))
    text = '\n'.join(output) if output else 'No files found matching pattern'
    if incomplete:
        text += f'\n[find: incomplete; {incomplete}]'
    return text + skipped.render('find')


def create_session_find_tool(*, settings=None, process_settings=None, progress_settings=None):
    settings = settings if settings is not None else FindSettings()
    runner = HostProcessRunner(process_settings)
    public, execution = build_tool_schemas('FindInput', _FindInput,
        pattern=(str, Field(min_length=1, max_length=settings.max_pattern_chars)),
        path=(str, Field(default='.', min_length=1, max_length=settings.max_path_chars)),
        limit=(int, Field(default=settings.default_limit, ge=1, le=settings.max_limit)))

    def find_files(pattern: str, runtime: ToolRuntime[dict, dict], path: str = '.',
                   limit: int = settings.default_limit, timeout: int | None = None) -> str:
        """Find file and directory names, including dotfiles, by fd glob and smart case.

        Listing a name does not authorize reading or changing its contents.
        """
        return run_async_sync(lambda: afind_files(pattern, runtime, path, limit, timeout))

    async def afind_files(pattern: str, runtime: ToolRuntime[dict, dict], path: str = '.',
                          limit: int = settings.default_limit, timeout: int | None = None) -> str:
        root = runtime.context.get('workspace_root')
        if not isinstance(root, Path) or not root.is_absolute():
            raise ToolException(FIND_NO_WORKSPACE)
        target = require_file_target(runtime, path, operation='list')
        progress = ToolProgress(runtime, progress_settings)
        try:
            return await _find_files(target, pattern, limit, runtime,
                settings=settings, runner=runner, progress=progress, timeout=timeout)
        finally:
            progress.flush()

    item = StructuredTool.from_function(find_files, afind_files, name='find', args_schema=execution)
    item.handle_validation_error = FIND_INVALID_ARGS.format(max_limit=settings.max_limit)
    item.description += (f' Default: {settings.default_limit} entries; maximum limit {settings.max_limit}, '
                         f'{settings.max_output_bytes} output bytes. '
                         'No default execution time limit; omit timeout or use 0 for unlimited execution, '
                         'or a positive integer for seconds. '
                         'Reports skipped and incomplete ranges; directory links are not followed recursively.')
    return ToolSpec(item, public, side_effects='read_only',
                    permissions=FilePermission('path', 'list', FIND_UNAVAILABLE, FIND_NO_WORKSPACE))
