"""Native rg streaming over authorized file groups, without content pre-reading."""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path

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
    argument_bytes, check_search, inside_git, safe_text, stream_records)
from .settings import GrepSettings
from .progress import ToolProgress


logger = logging.getLogger(__name__)
GREP_UNAVAILABLE = ('grep cannot search this path: it may be missing, not a readable '
                    'file or directory, or blocked by workspace policy. Check the path.')
GREP_NO_RG = 'grep requires ripgrep (rg), which is unavailable. Install rg and retry.'
GREP_NO_WORKSPACE = 'grep has no workspace for this session. Select a workspace first.'
GREP_INVALID_ARGS = ('Invalid grep arguments. Provide a non-empty pattern; optional path/glob as '
    'non-empty strings; ignoreCase/literal as booleans; context from 0 to {max_context}; '
    'limit from 1 to {max_limit}; optional timeout as a nonnegative integer in seconds '
    '(omit, null, or 0 for no time limit); remove unknown fields.')


class _GrepInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    pattern: str = Field(min_length=1)
    path: str = Field(default='.', min_length=1)
    glob: str | None = None
    ignoreCase: bool = False
    literal: bool = False
    context: int = Field(ge=0)
    limit: int = Field(ge=1)
    timeout: ExecutionTimeout = None


def _native_failure(diagnostics):
    detail = bytes(diagnostics.buffer).lower()
    if b'error parsing glob' in detail or b'invalid glob' in detail:
        return ToolException('grep needs a valid file glob such as *.py or src/**/*.py.')
    if b'regex parse error' in detail or b'invalid regex' in detail or b'error parsing regex' in detail:
        return ToolException('grep could not parse this regex. Correct the pattern or set literal=true.')
    return ToolException('grep could not complete a native file search. Check file availability, pattern and access.')


def _native_bytes(value):
    if not isinstance(value, dict):
        raise ValueError('invalid_record')
    if isinstance(value.get('text'), str):
        return value['text'].encode('utf-8')
    if isinstance(value.get('bytes'), str):
        return base64.b64decode(value['bytes'], validate=True)
    raise ValueError('invalid_record')


class _SearchControl:
    """Descendant processes see cancellation of their owning search task too."""

    def __init__(self, source):
        self.source = source
        self.owner = asyncio.current_task()

    @property
    def requested(self):
        return bool(self.owner.cancelling() or self.source is not None and self.source.requested)


class _GrepSearch:
    def __init__(self, target, pattern, glob, ignore_case, literal, context, limit, runtime, settings, runner, progress=None, timeout=None):
        self.progress = progress or ToolProgress(None)
        self.candidates = 0
        self.target, self.runtime, self.settings, self.runner = target, runtime, settings, runner
        self.binary = resolve_tool_binary('rg')
        if self.binary is None:
            raise ToolException(GREP_NO_RG)
        self.root = target if target.is_dir() else target.parent
        self.control = _SearchControl(runtime.context.get('_run_control'))
        self.deadline = asyncio.get_running_loop().time() + timeout if timeout else None
        self.pattern, self.glob = pattern, glob
        self.ignore_case, self.literal, self.context, self.limit = ignore_case, literal, context, limit
        self.skipped = SkippedTargets(settings.max_output_bytes)
        self.rows = []
        self.output_bytes = self.matches = 0
        self.notes = set()
        self.incomplete = None
        self.group = {}
        self.searched = False
        self.group_bytes = argument_bytes(self._search_arguments([]))
        self.group_used_bytes = self.group_bytes

    def _search_arguments(self, paths):
        args = ['--no-config', '--json', '--line-number', '--color=never',
                '--max-count', str(self.limit + 1)]
        if self.ignore_case:
            args.append('--ignore-case')
        if self.literal:
            args.append('--fixed-strings')
        if self.context:
            args += ['-C', str(self.context)]
        if self.glob is not None:
            args += ['--glob', self.glob]
        return [self.binary, *args, '-e', self.pattern, '--', *paths]

    def _authorize(self, candidate):
        relative = candidate.relative_to(self.root).as_posix()
        try:
            subject = prepare_file_subject(str(candidate), self.root, 'search')
            allowed, reason = candidate_access(self.runtime, candidate, operation='search')
            if not allowed:
                self.skipped.add(f'{relative}: {reason}')
                return None
            real = verify_file_target(subject)
            if not real.is_file():
                raise ToolException('Not a regular file')
            return real, (relative, subject)
        except ToolException:
            self.skipped.add(f'{relative}: unavailable_target')
            self.incomplete = self.incomplete or 'some targets changed or became unavailable'
            return None

    def _recheck(self, subject):
        try:
            real = verify_file_target(subject)
            allowed, reason = candidate_access(self.runtime, Path(subject['requested']), operation='search')
            if allowed and real.is_file():
                return True
            self.skipped.add(f"{Path(subject['requested']).relative_to(self.root).as_posix()}: {reason}")
        except ToolException:
            self.skipped.add(f"{Path(subject['requested']).relative_to(self.root).as_posix()}: unavailable_target")
        self.incomplete = self.incomplete or 'some targets changed or became unavailable'
        return False

    async def _on_event(self, record, authorized):
        if record is None:
            self.incomplete = self.incomplete or 'a JSON record exceeded max_record_bytes and was omitted'
            return
        event = json.loads(record)
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise ValueError('invalid_record')
        kind, data = event['type'], event.get('data')
        if kind not in ('match', 'context', 'end'):
            return
        if not isinstance(data, dict):
            raise ValueError('invalid_record')
        path = Path(os.fsdecode(_native_bytes(data.get('path'))))
        if not path.is_absolute():
            path = self.root / path
        if path not in authorized:
            raise ValueError('unapproved_result_path')
        relative, subject = authorized[path]
        if kind == 'end':
            if data.get('binary_offset') is not None:
                self.notes.add('binary content reported by rg; affected files may not be fully searched')
                self.skipped.add(f'{relative}: binary content; rg stopped scanning this file')
            return
        number = data.get('line_number')
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError('invalid_record')
        if not await asyncio.to_thread(self._recheck, subject):
            return
        # rg can include bytes beyond the binary boundary on some platforms.
        raw = _native_bytes(data.get('lines')).split(b'\0', 1)[0]
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            text = raw.decode('utf-8', errors='replace')
            self.notes.add('non-UTF-8 match bytes displayed with replacement characters')
        text = text.rstrip('\r\n')
        if len(text) > self.settings.max_line_chars:
            text = text[:self.settings.max_line_chars]
            self.notes.add('long lines clipped; use read for full lines')
        if kind == 'match':
            if self.matches >= self.limit:
                raise SearchStopped('truncated at match limit')
            self.matches += 1
        separator = ':' if kind == 'match' else '-'
        line = f'{safe_text(relative)}{separator}{number}{separator} {safe_text(text)}'
        addition = len(line.encode()) + int(bool(self.rows))
        if self.output_bytes + addition > self.settings.max_output_bytes:
            raise SearchStopped('truncated at output byte budget')
        self.rows.append((line, subject))
        self.output_bytes += addition
        self.progress.report(phase='search_matches', completed=self.matches, unit='matches')

    async def _search_group(self, *, empty=False):
        authorized = {}
        for real, value in self.group.items():
            check_search(self.deadline, self.control)
            if await asyncio.to_thread(self._recheck, value[1]):
                authorized[real] = value
        self.group = {}
        self.group_used_bytes = self.group_bytes
        if not authorized and not empty:
            return
        paths = [str(path) for path in authorized] if authorized else ['-']
        argv = self._search_arguments(paths)
        if argument_bytes(argv) > self.settings.max_argument_bytes:
            raise SearchStopped('command argument budget exceeded; narrow the pattern or path')
        parser = RecordBuffer(b'\n', self.settings.max_record_bytes)
        diagnostics = DiagnosticTail(self.settings.max_diagnostic_bytes)
        self.progress.report(phase='search_group', completed=0, unit='matches')
        async def on_record(record):
            await self._on_event(record, authorized)
        result = await stream_records(self.runner, argv, self.root, self.deadline, self.control,
                                      parser, on_record, diagnostics)
        self.searched = True
        if result.returncode not in (0, 1):
            logger.warning('grep_native_failed phase=search returncode=%s', result.returncode)
            raise _native_failure(diagnostics)

    async def _discover_candidates(self):
        async def candidate(record):
            if record is None:
                self.incomplete = self.incomplete or 'a path record exceeded the configured path budget'
                return
            if not record:
                raise ValueError('invalid_record')
            path = Path(os.fsdecode(record))
            if not path.is_absolute():
                path = self.root / path
            value = await asyncio.to_thread(self._authorize, path)
            self.candidates += 1
            self.progress.report(phase='discover_candidates', completed=self.candidates, unit='entries')
            check_search(self.deadline, self.control)
            if value is None:
                return
            real, identity = value
            cost = argument_bytes([str(real)])
            if self.group and self.group_used_bytes + cost > self.settings.max_argument_bytes:
                await self._search_group()
            if self.group_bytes + cost > self.settings.max_argument_bytes:
                raise SearchStopped('a path exceeds the command argument budget')
            if real not in self.group:
                self.group_used_bytes += cost
                self.group[real] = identity

        argv = [self.binary, '--no-config', '--files', '--hidden', '--null']
        if not inside_git(self.root):
            argv.append('--no-require-git')
        if self.glob is not None:
            argv += ['--glob', self.glob]
        argv += ['--', str(self.target)]
        result = await stream_records(self.runner, argv, self.root, self.deadline, self.control,
            RecordBuffer(b'\0', self.settings.max_record_bytes), candidate,
            diagnostics := DiagnosticTail(self.settings.max_diagnostic_bytes))
        if result.returncode not in (0, 1):
            logger.warning('grep_native_failed phase=discovery returncode=%s', result.returncode)
            raise _native_failure(diagnostics)

    async def run(self):
        self.progress.report(phase='discover_candidates', completed=0, unit='entries')
        if not self.pattern.strip() or '\x00' in self.pattern or self.glob is not None and '\x00' in self.glob:
            raise ToolException(GREP_INVALID_ARGS.format(max_context=self.settings.max_context_lines,
                                                       max_limit=self.settings.max_limit))
        try:
            await self._discover_candidates()
            if self.group:
                await self._search_group()
            if not self.searched:
                # Same search path against empty stdin validates regex even with zero candidates.
                await self._search_group(empty=True)
        except SearchStopped as error:
            self.incomplete = str(error)
        except ValueError as error:
            logger.warning('grep_protocol_failed error_type=%s', type(error).__name__)
            raise ToolException('grep received incomplete or invalid native search records. No complete result is available.') from error
        except OSError as error:
            logger.warning('grep_io_failed error_type=%s', type(error).__name__)
            raise ToolException('grep could not start or search due to an I/O error. Check rg and file access.') from error
        lines, checked = [], {}
        for line, subject in self.rows:
            key = subject['requested']
            if key not in checked:
                checked[key] = await asyncio.to_thread(self._recheck, subject)
            if checked[key]:
                lines.append(line)
        if self.incomplete and not lines:
            raise ToolException(f'grep could not complete: {self.incomplete}. No complete result is available.'
                                + self.skipped.render('grep'))
        text = '\n'.join(lines) if lines else 'No matches found'
        if self.incomplete:
            text += f'\n[grep: incomplete; {self.incomplete}]'
        for note in sorted(self.notes):
            text += f'\n[grep: {note}]'
        return text + self.skipped.render('grep')


async def _grep_files(target, pattern, glob, ignore_case, literal, context, limit, runtime, *, settings, runner, progress=None, timeout=None):
    return await _GrepSearch(target, pattern, glob, ignore_case, literal, context,
                             limit, runtime, settings, runner, progress, timeout).run()


def create_session_grep_tool(*, settings=None, process_settings=None, progress_settings=None):
    settings = settings if settings is not None else GrepSettings()
    runner = HostProcessRunner(process_settings)
    public, execution = build_tool_schemas('GrepInput', _GrepInput,
        pattern=(str, Field(min_length=1, max_length=settings.max_pattern_chars)),
        path=(str, Field(default='.', min_length=1, max_length=settings.max_path_chars)),
        glob=(str | None, Field(default=None, min_length=1, max_length=settings.max_glob_chars)),
        context=(int, Field(default=settings.default_context_lines, ge=0, le=settings.max_context_lines)),
        limit=(int, Field(default=settings.default_limit, ge=1, le=settings.max_limit)))

    def search_files(pattern: str, runtime: ToolRuntime[dict, dict], path: str = '.',
                     glob: str | None = None, ignoreCase: bool = False, literal: bool = False,
                     context: int = settings.default_context_lines, limit: int = settings.default_limit,
                     timeout: int | None = None) -> str:
        """Search authorized files with native rg regex, literal, glob and context."""
        return run_async_sync(lambda: asearch_files(pattern, runtime, path, glob, ignoreCase, literal, context, limit, timeout))

    async def asearch_files(pattern: str, runtime: ToolRuntime[dict, dict], path: str = '.',
                            glob: str | None = None, ignoreCase: bool = False, literal: bool = False,
                            context: int = settings.default_context_lines, limit: int = settings.default_limit,
                            timeout: int | None = None) -> str:
        root = runtime.context.get('workspace_root')
        if not isinstance(root, Path) or not root.is_absolute():
            raise ToolException(GREP_NO_WORKSPACE)
        target = require_file_target(runtime, path, operation='search')
        progress = ToolProgress(runtime, progress_settings)
        try:
            return await _grep_files(target, pattern, glob, ignoreCase, literal, context, limit, runtime,
                                     settings=settings, runner=runner, progress=progress, timeout=timeout)
        finally:
            progress.flush()

    item = StructuredTool.from_function(search_files, asearch_files, name='grep', args_schema=execution)
    item.handle_validation_error = GREP_INVALID_ARGS.format(max_context=settings.max_context_lines,
                                                           max_limit=settings.max_limit)
    item.description += (f' Default: {settings.default_limit} matches; maximum limit {settings.max_limit}, '
                         f'{settings.max_output_bytes} output bytes. '
                         'No default execution time limit; omit timeout or use 0 for unlimited execution, '
                         'or a positive integer for seconds across discovery and all search groups. '
                         'Reports skipped or incomplete ranges; no recursive following of directory links.')
    return ToolSpec(item, public, side_effects='read_only',
                    permissions=FilePermission('path', 'search', GREP_UNAVAILABLE, GREP_NO_WORKSPACE))
