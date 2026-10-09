"""Bounded workspace text search with Read's file-content policy."""

import codecs
import json
import logging
from pathlib import Path
import shutil
import subprocess
import time

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.agent_runtime.tool_approval import (
    ApprovalPolicy, attach_approval, review_path,
)

from .find import _discover_paths, _glob_matches, _validate_glob
from .ls import classify_ls_target
from .read import (
    ReadOnlyApprovalGrants, _has_disallowed_hidden_part, classify_read_target,
    resolve_workspace_root,
)


logger = logging.getLogger(__name__)
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
MAX_CONTEXT = 10
MAX_OUTPUT_BYTES = 50 * 1024
MAX_LINE_CHARS = 2000
MAX_CANDIDATES = 2000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES = 32 * 1024 * 1024
GREP_UNAVAILABLE = ('grep cannot search this path: it may be missing, not a readable '
                    'text file or directory, or blocked by workspace policy. Check '
                    'the path or choose an allowed target.')
GREP_OUTSIDE = ('grep cannot read this file outside the workspace without a valid '
                'approval. Request approval for the current file or choose one '
                'inside the workspace.')
GREP_OUTSIDE_DIRECTORY = ('grep cannot search an entire directory outside the workspace. '
                          'Choose a single file and request approval, or search '
                          'a directory inside the workspace.')
GREP_NO_RG = ('grep requires ripgrep (rg), which is unavailable. Install rg and retry.')
GREP_INVALID_REGEX = ('grep could not parse this regex. Correct the pattern or set '
                      'literal=true to search for the exact text.')
GREP_INVALID_GLOB = ('grep needs a valid file glob such as *.py or src/**/*.py. '
                     'Correct the glob and retry.')
GREP_NOT_TEXT = ('grep only searches valid UTF-8 text without non-text control bytes. '
                 'Choose a text file or use another supported tool.')
GREP_SEARCH_BUDGET = ('grep could not complete within the search budget. Narrow the '
                      'directory, add a file glob, or choose one file and retry.')
GREP_IO_FAILURE = ('grep could not complete a file search due to an I/O error. '
                   'Check file availability or choose another file.')
GREP_NO_WORKSPACE = ('grep has no workspace for this session. Select a workspace first.')
GREP_TOOL_FAILURE = ('grep failed unexpectedly; no matches were returned. Choose '
                     'another target or ask the user for help.')
GREP_INVALID_ARGS = ('Invalid grep arguments. Provide a non-empty pattern; optional '
                     'path and glob as non-empty strings; ignoreCase/literal as booleans; '
                     'context as an integer from 0 to 10; limit as an integer from '
                     '1 to 1000; remove unknown fields.')
SAFE_GREP_ERRORS = frozenset((GREP_UNAVAILABLE, GREP_OUTSIDE, GREP_OUTSIDE_DIRECTORY,
                              GREP_NO_RG, GREP_INVALID_REGEX, GREP_INVALID_GLOB,
                              GREP_NOT_TEXT,
                              GREP_SEARCH_BUDGET, GREP_IO_FAILURE, GREP_NO_WORKSPACE,
                              GREP_TOOL_FAILURE, GREP_INVALID_ARGS))


class GrepInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    pattern: str = Field(min_length=1, max_length=4096)
    path: str = Field(default='.', min_length=1, max_length=4096)
    glob: str | None = Field(default=None, min_length=1, max_length=4096)
    ignoreCase: bool = False
    literal: bool = False
    context: int = Field(default=0, ge=0, le=MAX_CONTEXT)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


class SessionGrepInput(GrepInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)

    runtime: ToolRuntime[dict, dict]


def grep_requires_approval(request) -> bool:
    context = request.runtime.context
    root = context.get('workspace_root') if isinstance(context, dict) else None
    args = request.tool_call.get('args', {})
    path = args.get('path', '.') if isinstance(args, dict) else None
    return (isinstance(root, Path) and isinstance(path, str)
            and classify_read_target(root, path)[0] == 'ask')


def _check_text(path: Path) -> int:
    """Reject content Read would not expose, without loading the whole file."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('search_budget')
    decoder = codecs.getincrementaldecoder('utf-8')('strict')
    size = 0
    with path.open('rb') as source:
        while chunk := source.read(64 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ValueError('search_budget')
            decoder.decode(chunk)
            if any((byte < 32 and byte not in (9, 10, 13)) or byte == 127
                   for byte in chunk):
                raise ValueError('not_text')
        decoder.decode(b'', final=True)
    return size


def _search_file(binary: str, file_path: Path, pattern: str, ignore_case: bool,
                 literal: bool, context: int, remaining: int, timeout: float):
    command = [binary, '--json', '--line-number', '--color=never',
               '--max-columns', str(MAX_LINE_CHARS), '--max-columns-preview',
               '--max-count', str(remaining)]
    if ignore_case:
        command.append('--ignore-case')
    if literal:
        command.append('--fixed-strings')
    if context:
        command.extend(['-C', str(context)])
    command.extend(['-e', pattern, '--', str(file_path)])
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError('search_budget') from error
    except OSError as error:
        raise OSError('rg_launch_failed') from error
    if result.returncode not in (0, 1):
        if b'regex parse error' in result.stderr or b'invalid regex' in result.stderr:
            raise ValueError('invalid_regex')
        raise OSError('rg_search_failed')
    return result.stdout.splitlines()


def _validate_regex(binary: str, pattern: str) -> None:
    """Parse with the same regex engine even when no eligible files exist."""
    try:
        result = subprocess.run([binary, '--quiet', '-e', pattern, '--', '-'],
                                input=b'', capture_output=True, timeout=5,
                                check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError('search_budget') from error
    except OSError as error:
        raise OSError('rg_launch_failed') from error
    if result.returncode == 2:
        if b'regex parse error' in result.stderr or b'invalid regex' in result.stderr:
            raise ValueError('invalid_regex')
        raise OSError('rg_pattern_check_failed')


def _grep_files(root: Path, pattern: str, path: str, glob: str | None,
                ignore_case: bool, literal: bool, context: int, limit: int,
                grants: ReadOnlyApprovalGrants | None = None,
                tool_call_id: str | None = None) -> str:
    try:
        if not pattern.strip():
            raise ValueError('invalid_pattern')
        if glob is not None:
            _validate_glob(glob)
        binary = shutil.which('rg')
        if binary is None:
            raise ValueError('rg_missing')
        if not literal:
            _validate_regex(binary, pattern)
        requested = Path(path)
        if '..' in requested.parts:
            raise ValueError('grep_denied')
        candidate = (requested if requested.is_absolute() else root / requested).resolve(strict=True)
        if candidate.is_dir():
            decision, target = classify_ls_target(root, path)
            if decision == 'ask':
                raise ValueError('outside_directory')
            if decision != 'allow' or target is None:
                raise ValueError('grep_denied')
            relative_dir = target.relative_to(root)
            if _has_disallowed_hidden_part((*relative_dir.parts, 'file'),
                                           inside_workspace=True):
                raise ValueError('grep_denied')
            files = []
            for item in _discover_paths(target):
                try:
                    relative = item.relative_to(target).as_posix()
                except ValueError:
                    logger.warning('grep_candidate_outside_search_root')
                    continue
                if glob is not None and not _glob_matches(relative, glob):
                    continue
                if classify_read_target(root, str(item))[0] == 'allow':
                    files.append((item, relative))
        else:
            decision, target = classify_read_target(root, path)
            if decision == 'deny' or target is None:
                raise ValueError('grep_denied')
            if decision == 'ask' and (grants is None
                                      or not grants.consume(tool_call_id, target)):
                raise ValueError('approval_missing_or_changed')
            if glob is not None and not _glob_matches(target.name, glob):
                return 'No matches found'
            files = [(target, target.name)]
        if len(files) > MAX_CANDIDATES:
            raise ValueError('search_budget')
        files.sort(key=lambda item: (item[1].casefold(), item[1]))

        output: list[str] = []
        output_bytes = 0
        match_count = 0
        scanned_bytes = 0
        skipped_files = 0
        truncated = False
        line_truncated = False
        deadline = time.monotonic() + 20
        for file_path, relative in files:
            if time.monotonic() >= deadline:
                raise ValueError('search_budget')
            try:
                scanned_bytes += _check_text(file_path)
            except (UnicodeError, ValueError, OSError) as error:
                if len(files) == 1:
                    if isinstance(error, ValueError) and str(error) == 'search_budget':
                        raise
                    if isinstance(error, OSError):
                        raise
                    raise ValueError('not_text') from error
                logger.warning('grep_skipped_file error_type=%s', type(error).__name__)
                skipped_files += 1
                continue
            if scanned_bytes > MAX_SCAN_BYTES:
                raise ValueError('search_budget')
            timeout = max(0.1, min(5, deadline - time.monotonic()))
            events = _search_file(binary, file_path, pattern, ignore_case, literal,
                                  context, limit + 1 - match_count, timeout)
            for event_line in events:
                try:
                    event = json.loads(event_line)
                except (ValueError, UnicodeError) as error:
                    logger.warning('grep_event_parse_failed error_type=%s', type(error).__name__)
                    raise ValueError('search_failure') from error
                kind = event.get('type')
                if kind not in ('match', 'context'):
                    continue
                data = event.get('data', {})
                if not isinstance(data.get('lines', {}).get('text'), str):
                    continue
                if kind == 'match':
                    match_count += 1
                    if match_count > limit:
                        truncated = True
                        break
                raw_text = data['lines']['text'].rstrip('\r\n')
                if len(raw_text) > MAX_LINE_CHARS:
                    raw_text = raw_text[:MAX_LINE_CHARS]
                    line_truncated = True
                safe_path = json.dumps(relative, ensure_ascii=False)[1:-1]
                safe_text = json.dumps(raw_text, ensure_ascii=False)[1:-1]
                separator = ':' if kind == 'match' else '-'
                line = f"{safe_path}{separator}{data['line_number']}{separator} {safe_text}"
                addition = len(line.encode('utf-8')) + int(bool(output))
                if output_bytes + addition > MAX_OUTPUT_BYTES:
                    truncated = True
                    break
                output.append(line)
                output_bytes += addition
            if truncated or match_count >= limit:
                truncated = True
                break
        result = '\n'.join(output) if output else 'No matches found'
        if truncated:
            result += '\n[grep: truncated; narrow the search or lower context]'
        if line_truncated:
            result += '\n[grep: long lines clipped; use read for full lines]'
        if skipped_files:
            result += f'\n[grep: {skipped_files} unreadable or oversized files skipped]'
        return result
    except ValueError as error:
        message = (GREP_OUTSIDE if str(error) == 'approval_missing_or_changed'
                   else GREP_OUTSIDE_DIRECTORY if str(error) == 'outside_directory'
                   else GREP_NO_RG if str(error) == 'rg_missing'
                   else GREP_INVALID_REGEX if str(error) == 'invalid_regex'
                   else GREP_INVALID_GLOB if str(error) == 'invalid_glob'
                   else GREP_INVALID_ARGS if str(error) == 'invalid_pattern'
                   else GREP_NOT_TEXT if str(error) == 'not_text'
                   else GREP_SEARCH_BUDGET if str(error) == 'search_budget'
                   else GREP_UNAVAILABLE)
        logger.warning('grep_failed error_type=%s', type(error).__name__)
        raise ToolException(message) from error
    except OSError as error:
        logger.warning('grep_failed error_type=%s', type(error).__name__)
        raise ToolException(GREP_IO_FAILURE) from error
    except GraphBubbleUp:
        raise
    except Exception as error:
        logger.warning('grep_failed error_type=%s', type(error).__name__)
        raise ToolException(GREP_TOOL_FAILURE) from error


def _safe_grep_error(error: ToolException) -> str:
    message = error.args[0] if error.args else None
    return message if isinstance(message, str) and message in SAFE_GREP_ERRORS else GREP_TOOL_FAILURE


def _set_safe_errors(search_files):
    search_files.handle_validation_error = GREP_INVALID_ARGS
    search_files.handle_tool_error = _safe_grep_error
    return search_files


def create_grep_tool(workspace_root: str | Path):
    root = resolve_workspace_root(workspace_root)

    @tool('grep', args_schema=GrepInput)
    def search_files(pattern: str, path: str = '.', glob: str | None = None,
                     ignoreCase: bool = False, literal: bool = False,
                     context: int = 0, limit: int = DEFAULT_LIMIT) -> str:
        """Search readable UTF-8 files with regex or literal text; respects .gitignore."""
        return _grep_files(root, pattern, path, glob, ignoreCase, literal, context, limit)

    return _set_safe_errors(search_files)


def create_session_grep_tool():
    @tool('grep', args_schema=SessionGrepInput)
    def search_files(pattern: str, runtime: ToolRuntime[dict, dict], path: str = '.',
                     glob: str | None = None, ignoreCase: bool = False,
                     literal: bool = False, context: int = 0,
                     limit: int = DEFAULT_LIMIT) -> str:
        """Search session UTF-8 files; outside single files need approval."""
        run_context = runtime.context
        root = run_context.get('workspace_root') if isinstance(run_context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('grep_failed error_type=MissingWorkspaceContext')
            raise ToolException(GREP_NO_WORKSPACE)
        grants = run_context.get('approval_grants', {}).get('grep')
        if not isinstance(grants, ReadOnlyApprovalGrants):
            grants = None
        return _grep_files(root, pattern, path, glob, ignoreCase, literal,
                           context, limit, grants, runtime.tool_call_id)

    return attach_approval(_set_safe_errors(search_files), ApprovalPolicy(
        when=grep_requires_approval,
        description='Search one file outside the session workspace',
        review=lambda root, args: review_path(root, args, classify_read_target, default='.'),
        grant_factory=ReadOnlyApprovalGrants,
    ))
