"""Bounded glob discovery of workspace file names using ripgrep ignore rules."""

from fnmatch import fnmatchcase
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.agent_runtime.tool_approval import (
    ApprovalPolicy, attach_approval, review_path,
)

from .ls import classify_ls_target
from .read import ReadOnlyApprovalGrants, resolve_workspace_root


logger = logging.getLogger(__name__)
DEFAULT_LIMIT = 1000
MAX_LIMIT = 2000
MAX_OUTPUT_BYTES = 50 * 1024
MAX_DISCOVERY_BYTES = 8 * 1024 * 1024
FIND_UNAVAILABLE = ('find cannot search this path: it may be missing, not a directory, '
                    'or blocked by workspace policy. Check the path or choose an allowed '
                    'directory.')
FIND_OUTSIDE = ('find cannot search this directory outside the workspace without a valid '
                'approval. Request approval for the current target or choose a directory '
                'inside the workspace.')
FIND_NO_RG = ('find requires ripgrep (rg), which is unavailable. Install rg and retry.')
FIND_INVALID_GLOB = ('find needs a valid non-empty glob pattern such as *.py or '
                     'src/**/*.py. Correct the pattern and retry.')
FIND_SEARCH_BUDGET = ('find could not complete within the search budget. Narrow the '
                      'directory or pattern and retry.')
FIND_IO_FAILURE = ('find could not list candidate files due to an I/O or search error. '
                   'Check directory availability or choose another directory.')
FIND_NO_WORKSPACE = ('find has no workspace for this session. Select a workspace first.')
FIND_TOOL_FAILURE = ('find failed unexpectedly; no file names were returned. Choose '
                     'another directory or ask the user for help.')
FIND_INVALID_ARGS = ('Invalid find arguments. Provide pattern as a non-empty glob string; '
                     'path as a non-empty directory string when present; limit as an '
                     'integer from 1 to 2000; remove unknown fields.')
SAFE_FIND_ERRORS = frozenset((FIND_UNAVAILABLE, FIND_OUTSIDE, FIND_NO_RG, FIND_INVALID_GLOB,
                              FIND_SEARCH_BUDGET, FIND_IO_FAILURE, FIND_NO_WORKSPACE,
                              FIND_TOOL_FAILURE))


class FindInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    pattern: str = Field(min_length=1, max_length=4096)
    path: str = Field(default='.', min_length=1, max_length=4096)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


class SessionFindInput(FindInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)

    runtime: ToolRuntime[dict, dict]


def find_requires_approval(request) -> bool:
    context = request.runtime.context
    root = context.get('workspace_root') if isinstance(context, dict) else None
    args = request.tool_call.get('args', {})
    path = args.get('path', '.') if isinstance(args, dict) else None
    return (isinstance(root, Path) and isinstance(path, str)
            and classify_ls_target(root, path)[0] == 'ask')


def _validate_glob(pattern: str) -> None:
    if (not pattern.strip() or '\x00' in pattern or '..' in PurePosixPath(pattern).parts
            or pattern.count('[') != pattern.count(']')):
        raise ValueError('invalid_glob')


def _glob_matches(relative: str, pattern: str) -> bool:
    if '/' not in pattern:
        return fnmatchcase(relative.rsplit('/', 1)[-1], pattern)
    components = relative.split('/')
    positions = {0}
    for part in pattern.split('/'):
        if part == '**':
            positions = {end for start in positions
                         for end in range(start, len(components) + 1)}
        else:
            positions = {index + 1 for index in positions
                         if index < len(components) and fnmatchcase(components[index], part)}
    return len(components) in positions


def _discover_paths(directory: Path) -> list[Path]:
    """Let rg apply ignore rules before callers match globs or read contents."""
    binary = shutil.which('rg')
    if binary is None:
        raise ValueError('rg_missing')
    inside_git = any((ancestor / '.git').exists()
                     for ancestor in (directory, *directory.parents))
    command = [binary, '--files', '--hidden', '--null']
    if not inside_git:
        command.append('--no-require-git')
    command.extend(['--', str(directory)])
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError('search_budget') from error
    except OSError as error:
        raise OSError('rg_launch_failed') from error
    if result.returncode not in (0, 1):
        raise OSError('rg_files_failed')
    if len(result.stdout) > MAX_DISCOVERY_BYTES:
        raise ValueError('search_budget')
    return [Path(os.fsdecode(raw)) for raw in result.stdout.split(b'\x00') if raw]


def _find_files(root: Path, pattern: str, path: str, limit: int,
                grants: ReadOnlyApprovalGrants | None = None,
                tool_call_id: str | None = None) -> str:
    try:
        _validate_glob(pattern)
        decision, target = classify_ls_target(root, path)
        if decision == 'deny' or target is None:
            raise ValueError('find_denied')
        if decision == 'ask' and (grants is None or not grants.consume(tool_call_id, target)):
            raise ValueError('approval_missing_or_changed')
        names = []
        for candidate in _discover_paths(target):
            try:
                relative = candidate.relative_to(target).as_posix()
            except ValueError:
                logger.warning('find_candidate_outside_search_root')
                continue
            if _glob_matches(relative, pattern):
                names.append(relative)
        names.sort(key=lambda name: (name.casefold(), name))
        if not names:
            return 'No files found matching pattern'
        lines = []
        size = 0
        truncated = False
        for name in names:
            if len(lines) >= limit:
                truncated = True
                break
            display = json.dumps(name, ensure_ascii=False)[1:-1]
            addition = len(display.encode('utf-8')) + int(bool(lines))
            if size + addition > MAX_OUTPUT_BYTES:
                truncated = True
                break
            lines.append(display)
            size += addition
        output = '\n'.join(lines)
        if truncated:
            output += '\n[find: truncated; narrow the pattern or directory]'
        return output
    except ValueError as error:
        message = (FIND_OUTSIDE if str(error) == 'approval_missing_or_changed'
                   else FIND_NO_RG if str(error) == 'rg_missing'
                   else FIND_INVALID_GLOB if str(error) == 'invalid_glob'
                   else FIND_SEARCH_BUDGET if str(error) == 'search_budget'
                   else FIND_UNAVAILABLE)
        logger.warning('find_failed error_type=%s', type(error).__name__)
        raise ToolException(message) from error
    except OSError as error:
        logger.warning('find_failed error_type=%s', type(error).__name__)
        raise ToolException(FIND_IO_FAILURE) from error
    except GraphBubbleUp:
        raise
    except Exception as error:
        logger.warning('find_failed error_type=%s', type(error).__name__)
        raise ToolException(FIND_TOOL_FAILURE) from error


def _safe_find_error(error: ToolException) -> str:
    message = error.args[0] if error.args else None
    return message if isinstance(message, str) and message in SAFE_FIND_ERRORS else FIND_TOOL_FAILURE


def _set_safe_errors(find_files):
    find_files.handle_validation_error = FIND_INVALID_ARGS
    find_files.handle_tool_error = _safe_find_error
    return find_files


def create_find_tool(workspace_root: str | Path):
    root = resolve_workspace_root(workspace_root)

    @tool('find', args_schema=FindInput)
    def find_files(pattern: str, path: str = '.', limit: int = DEFAULT_LIMIT) -> str:
        """Find file names by glob, honoring .gitignore; outside directories need approval."""
        return _find_files(root, pattern, path, limit)

    return _set_safe_errors(find_files)


def create_session_find_tool():
    @tool('find', args_schema=SessionFindInput)
    def find_files(pattern: str, runtime: ToolRuntime[dict, dict], path: str = '.',
                   limit: int = DEFAULT_LIMIT) -> str:
        """Find file names by glob in the session workspace; no file content is read."""
        context = runtime.context
        root = context.get('workspace_root') if isinstance(context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('find_failed error_type=MissingWorkspaceContext')
            raise ToolException(FIND_NO_WORKSPACE)
        grants = context.get('approval_grants', {}).get('find')
        if not isinstance(grants, ReadOnlyApprovalGrants):
            grants = None
        return _find_files(root, pattern, path, limit, grants, runtime.tool_call_id)

    return attach_approval(_set_safe_errors(find_files), ApprovalPolicy(
        when=find_requires_approval,
        description='Find file names outside the session workspace',
        review=lambda root, args: review_path(root, args, classify_ls_target, default='.'),
        grant_factory=ReadOnlyApprovalGrants,
    ))
