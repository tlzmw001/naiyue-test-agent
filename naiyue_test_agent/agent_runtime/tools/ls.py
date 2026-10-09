"""Bounded directory names for one trusted local workspace."""

import json
import logging
import os
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.agent_runtime.tool_approval import (
    ApprovalPolicy, attach_approval, review_path,
)

from .read import ReadOnlyApprovalGrants, resolve_workspace_root


logger = logging.getLogger(__name__)
DEFAULT_LIMIT = 500
MAX_LIMIT = 2000
MAX_LS_BYTES = 50 * 1024
LS_UNAVAILABLE = ('ls cannot list this path: it may be missing, not a directory, or '
                  'blocked by workspace policy. Check the path or choose an allowed directory.')
LS_OUTSIDE = ('ls cannot list this directory outside the workspace without a valid '
              'approval. Request approval for the current target or choose a directory '
              'inside the workspace.')
LS_IO_FAILURE = ('ls could not complete the directory listing due to an I/O error. '
                 'Check directory availability or choose another directory.')
LS_NO_WORKSPACE = ('ls has no workspace for this session. Select a workspace before '
                   'listing directories.')
LS_TOOL_FAILURE = ('ls failed unexpectedly; no directory entries were returned. '
                   'Choose another directory or ask the user for help.')
LS_INVALID_ARGS = ('Invalid ls arguments. Provide path as a non-empty string when present; '
                   'limit as an integer from 1 to 2000 when present; remove unknown fields.')
SAFE_LS_ERRORS = frozenset((LS_UNAVAILABLE, LS_OUTSIDE, LS_IO_FAILURE,
                            LS_NO_WORKSPACE, LS_TOOL_FAILURE))


class LsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(default='.', min_length=1, max_length=4096)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


class SessionLsInput(LsInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)

    runtime: ToolRuntime[dict, dict]


def classify_ls_target(root: Path, path: str) -> tuple[str, Path | None]:
    """Resolve a directory without listing it; outside directories need approval."""
    try:
        requested = Path(path)
        if '..' in requested.parts:
            raise ValueError('traversal')
        target = (requested if requested.is_absolute() else root / requested).resolve(strict=True)
        if not target.is_dir():
            raise ValueError('not_directory')
        try:
            target.relative_to(root)
        except ValueError:
            return 'ask', target
        return 'allow', target
    except (OSError, ValueError, UnicodeError, RuntimeError, TypeError):
        return 'deny', None


def ls_requires_approval(request) -> bool:
    context = request.runtime.context
    root = context.get('workspace_root') if isinstance(context, dict) else None
    args = request.tool_call.get('args', {})
    path = args.get('path', '.') if isinstance(args, dict) else None
    return (isinstance(root, Path) and isinstance(path, str)
            and classify_ls_target(root, path)[0] == 'ask')


def _list_directory(root: Path, path: str, limit: int,
                    grants: ReadOnlyApprovalGrants | None = None,
                    tool_call_id: str | None = None) -> str:
    try:
        decision, target = classify_ls_target(root, path)
        if decision == 'deny' or target is None:
            raise ValueError('ls_denied')
        if decision == 'ask' and (grants is None or not grants.consume(tool_call_id, target)):
            raise ValueError('ls_approval_missing_or_changed')

        with os.scandir(target) as scan:
            entries = sorted(scan, key=lambda entry: (entry.name.casefold(), entry.name))
        lines: list[str] = []
        byte_count = 0
        truncated_by = None
        for entry in entries:
            try:
                is_directory = entry.is_dir(follow_symlinks=True)
                # Pi skips entries that disappear before they can be inspected.
                entry.stat(follow_symlinks=True)
            except OSError as error:
                logger.warning('ls_entry_stat_failed error_type=%s', type(error).__name__)
                continue
            if len(lines) >= limit:
                truncated_by = f'{limit} entries'
                break
            name = json.dumps(entry.name, ensure_ascii=False)[1:-1]
            line = name + ('/' if is_directory else '')
            addition = len(line.encode('utf-8')) + int(bool(lines))
            if byte_count + addition > MAX_LS_BYTES:
                truncated_by = '50 KiB output'
                break
            lines.append(line)
            byte_count += addition

        if not lines and truncated_by is None:
            return '(empty directory)'
        body = '\n'.join(lines)
        if truncated_by is not None:
            body += f'\n[ls: truncated at {truncated_by}; narrow the directory or limit]'
        return body
    except ValueError as error:
        message = (LS_OUTSIDE if str(error) == 'ls_approval_missing_or_changed'
                   else LS_UNAVAILABLE)
        logger.warning('ls_failed error_type=%s', type(error).__name__)
        raise ToolException(message) from error
    except OSError as error:
        logger.warning('ls_failed error_type=%s', type(error).__name__)
        raise ToolException(LS_IO_FAILURE) from error
    except GraphBubbleUp:
        raise
    except Exception as error:
        logger.warning('ls_failed error_type=%s', type(error).__name__)
        raise ToolException(LS_TOOL_FAILURE) from error


def _safe_ls_error(error: ToolException) -> str:
    message = error.args[0] if error.args else None
    return message if isinstance(message, str) and message in SAFE_LS_ERRORS else LS_TOOL_FAILURE


def _set_safe_tool_errors(list_directory):
    list_directory.handle_validation_error = LS_INVALID_ARGS
    list_directory.handle_tool_error = _safe_ls_error
    return list_directory


def create_ls_tool(workspace_root: str | Path):
    """Create one ls tool bound to a canonical workspace root."""
    root = resolve_workspace_root(workspace_root)

    @tool('ls', args_schema=LsInput)
    def list_directory(path: str = '.', limit: int = DEFAULT_LIMIT) -> str:
        """List directory names, including hidden names, sorted with '/' for directories.

        Does not read file contents. Use path to choose a directory and limit to
        bound entries; an outside directory requires approval in a session.
        """
        return _list_directory(root, path, limit)

    return _set_safe_tool_errors(list_directory)


def create_session_ls_tool():
    """Create an ls tool whose workspace root comes from the trusted run context."""

    @tool('ls', args_schema=SessionLsInput)
    def list_directory(runtime: ToolRuntime[dict, dict], path: str = '.',
                       limit: int = DEFAULT_LIMIT) -> str:
        """List names in the session workspace, including hidden names."""
        context = runtime.context
        root = context.get('workspace_root') if isinstance(context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('ls_failed error_type=MissingWorkspaceContext')
            raise ToolException(LS_NO_WORKSPACE)
        grants = context.get('approval_grants', {}).get('ls')
        if not isinstance(grants, ReadOnlyApprovalGrants):
            grants = None
        return _list_directory(root, path, limit, grants, runtime.tool_call_id)

    return attach_approval(_set_safe_tool_errors(list_directory), ApprovalPolicy(
        when=ls_requires_approval,
        description='List a directory outside the session workspace',
        review=lambda root, args: review_path(root, args, classify_ls_target, default='.'),
        grant_factory=ReadOnlyApprovalGrants,
    ))
