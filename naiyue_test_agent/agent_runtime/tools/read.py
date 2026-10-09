"""A bounded UTF-8 reader for an explicitly chosen local workspace."""

import logging
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import ToolException
from langgraph.errors import GraphBubbleUp
from pydantic import BaseModel, ConfigDict, Field

from naiyue_test_agent.agent_runtime.tool_approval import (
    ApprovalPolicy, ContextualApprovalPolicy, ApprovalReview, OneUseApprovalGrants, attach_approval, review_path,
)
from naiyue_test_agent.agent_runtime.file_permissions import (
    FILE_OPERATION_KEY, FileAccessRequest, FileAccessDecision, decide_file_access,
)


logger = logging.getLogger(__name__)
MAX_READ_BYTES = 64 * 1024
MAX_PAGE_LINES = 2000
READ_UNAVAILABLE = ('read cannot access this path: it may be missing, not a regular '
                    'file, or blocked by workspace policy. Check the path or choose an '
                    'allowed file.')
READ_OUTSIDE = ('read cannot read this file outside the workspace without a valid '
                'approval. Request approval for the current target or choose a file '
                'inside the workspace.')
READ_IO_FAILURE = ('read could not complete the file read due to an I/O error. '
                   'Check file availability or choose another file.')
READ_NO_WORKSPACE = ('read has no workspace for this session. Select a workspace '
                     'before reading files.')
READ_TOOL_FAILURE = ('read failed unexpectedly; no content was returned. '
                     'Choose another file or ask the user for help.')
READ_POLICY_DENIED = ('read was denied by the configured file-access policy. '
                      'Choose an allowed file or ask the user to adjust the policy.')
READ_POLICY_UNAVAILABLE = ('read file-access policy is unavailable or returned an invalid decision. '
                           'No file content was read. Ask the user to check the policy; '
                           'do not retry the same request unchanged.')
INVALID_ARGS = ('Invalid read arguments. Provide path as a non-empty string; '
                'offset as an integer >= 1 and limit as an integer from 1 to 2000 '
                'when present; remove unknown fields.')
INVALID_ENCODING = ('read only supports UTF-8 text; this file is not valid UTF-8. '
                    'Choose a UTF-8 text file.')
NON_TEXT_BYTES = ('read only supports text; this file contains non-text control '
                  'bytes. Choose a text file.')
LINE_TOO_LONG = ('read cannot return a line larger than 64 KiB. Choose a different '
                 'file or ask the user to split the long line.')
SAFE_READ_ERRORS = frozenset((READ_UNAVAILABLE, INVALID_ENCODING, NON_TEXT_BYTES,
                              LINE_TOO_LONG, READ_OUTSIDE, READ_IO_FAILURE,
                              READ_NO_WORKSPACE, READ_TOOL_FAILURE,
                              READ_POLICY_DENIED, READ_POLICY_UNAVAILABLE))


class ReadInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')

    path: str = Field(min_length=1, max_length=4096)
    offset: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1, le=MAX_PAGE_LINES)


class SessionReadInput(ReadInput):
    model_config = ConfigDict(strict=True, extra='forbid', arbitrary_types_allowed=True)

    runtime: ToolRuntime[dict, dict]


ReadOnlyApprovalGrants = OneUseApprovalGrants


def resolve_workspace_root(workspace_root: str | Path) -> Path:
    """Validate and canonicalize a trusted application-provided root."""
    try:
        root = Path(workspace_root).expanduser().resolve(strict=True)
        credential_ancestor = any(part == '.env' or part.startswith('.env.')
                                  or part in ('.ssh', '.git') for part in root.parts)
        if not root.is_dir() or root.name.startswith('.') or credential_ancestor:
            raise ValueError('workspace must be an allowed existing directory')
    except (OSError, RuntimeError) as error:
        raise ValueError('workspace must be an existing directory') from error
    return root


def _has_disallowed_hidden_part(parts: tuple[str, ...], *, inside_workspace: bool) -> bool:
    for index, part in enumerate(parts):
        if not part.startswith('.'):
            continue
        if inside_workspace and part == '.github' and index < len(parts) - 1:
            continue
        if inside_workspace and part == '.gitignore' and index == len(parts) - 1:
            continue
        return True
    return False


def classify_read_target(root: Path, path: str) -> tuple[str, Path | None]:
    """Classify a canonical file target without reading file content."""
    try:
        requested = Path(path)
        if '..' in requested.parts:
            raise ValueError('traversal')
        try:
            supplied_relative = requested.relative_to(root) if requested.is_absolute() else requested
            supplied_inside = True
        except ValueError:
            supplied_relative = requested
            supplied_inside = False
        if _has_disallowed_hidden_part(supplied_relative.parts,
                                       inside_workspace=supplied_inside):
            raise ValueError('hidden_path')
        candidate = requested if requested.is_absolute() else root / requested
        target = candidate.resolve(strict=True)
        if target.suffix.lower() in ('.pem', '.key') or not target.is_file():
            raise ValueError('sensitive_or_not_file')
        try:
            relative = target.relative_to(root)
        except ValueError:
            if _has_disallowed_hidden_part(target.parts, inside_workspace=False):
                raise ValueError('hidden_path')
            return 'ask', target
        if _has_disallowed_hidden_part(relative.parts, inside_workspace=True):
            raise ValueError('hidden_path')
        return 'allow', target
    except (OSError, ValueError, UnicodeError, RuntimeError, TypeError):
        return 'deny', None


def read_requires_approval(request) -> bool:
    """Only an outside, otherwise permitted Read call pauses the agent."""
    context = request.runtime.context
    root = context.get('workspace_root') if isinstance(context, dict) else None
    args = request.tool_call.get('args', {})
    path = args.get('path') if isinstance(args, dict) else None
    return (isinstance(root, Path) and isinstance(path, str)
            and _classify_read_with_resources(
                root, path, context.get('read_resource_roots', ()),
            )[0] == 'ask')


def _classify_read_with_resources(root: Path, path: str, resources: tuple) -> tuple[str, Path | None]:
    """Exempt only registered canonical resource ancestry from hidden-path policy."""
    if not resources:
        return classify_read_target(root, path)
    try:
        requested = Path(path)
        if '..' in requested.parts or any(
            part.startswith('.env') or part in ('.ssh', '.git') for part in requested.parts
        ):
            return 'deny', None
        candidate = requested if requested.is_absolute() else root / requested
        target = candidate.resolve(strict=True)
        target.relative_to(root)
        if (not target.is_file() or target.suffix.lower() in ('.key', '.pem')
                or any(part.startswith('.env') or part in ('.ssh', '.git') for part in target.parts)):
            return 'deny', None
        for resource in resources:
            if not isinstance(resource, Path) or not resource.is_absolute():
                continue
            try:
                if resource.resolve(strict=True) != resource or not resource.is_dir():
                    continue
                resource.relative_to(root)
                relative = target.relative_to(resource)
            except ValueError:
                continue
            except (OSError, RuntimeError) as error:
                logger.warning('read_resource_scope_unavailable error_type=%s', type(error).__name__)
                continue
            try:
                supplied = candidate.relative_to(resource)
            except ValueError:
                supplied = relative
            if any(part.startswith('.') for part in (*relative.parts, *supplied.parts)):
                continue
            return 'allow', target
    except (OSError, ValueError, UnicodeError, RuntimeError, TypeError):
        # Normal outside-workspace approval remains governed by the original policy.
        return classify_read_target(root, path)
    return classify_read_target(root, path)


def _decode_read_text(content: bytes) -> str:
    text = content.decode('utf-8')
    if any((byte < 32 and byte not in (9, 10, 13)) or byte == 127 for byte in content):
        raise ValueError('non_text_content')
    return text


def _resolve_read_access(tool, context, path):
    root = context['workspace_root']
    kind, target = _classify_read_with_resources(root, path, context.get('read_resource_roots', ()))
    if target is None:
        return FileAccessDecision('deny'), None
    request = FileAccessRequest(tool.name, 'read', root, target, context.get('session_id'))
    return decide_file_access(tool, request, kind), target


def _read_policy_requires_approval(tool, request):
    context = request.runtime.context
    args = request.tool_call.get('args', {})
    path = args.get('path') if isinstance(args, dict) else None
    if not isinstance(context, dict) or not isinstance(context.get('workspace_root'), Path) or not isinstance(path, str):
        return False
    return _resolve_read_access(tool, context, path)[0].kind == 'ask'


def _review_read_policy(tool, root, args, context):
    if not isinstance(args, dict) or not isinstance(args.get('path'), str):
        return None
    trusted = {**context, 'workspace_root': root}
    decision, target = _resolve_read_access(tool, trusted, args['path'])
    return ApprovalReview(target, target) if decision.kind == 'ask' and target is not None else None


def _read(
    root: Path, path: str, offset: int | None, limit: int | None,
    grants: ReadOnlyApprovalGrants | None = None, tool_call_id: str | None = None,
    resources: tuple[Path, ...] = (),
    *, access: tuple[FileAccessDecision, Path | None] | None = None,
) -> str:
    try:
        if access is None:
            decision, target = _classify_read_with_resources(root, path, resources)
        else:
            choice, target = access
            if choice.reason == 'policy_denied':
                raise ValueError('policy_denied')
            if choice.reason == 'policy_unavailable':
                raise ValueError('policy_unavailable')
            decision = choice.kind
        if target is None or decision == 'deny':
            raise ValueError('read_denied')
        if decision == 'ask' and (grants is None or not grants.consume(tool_call_id, target)):
            raise ValueError('read_approval_missing_or_changed')
        with target.open('rb') as source:
            if offset is None and limit is None:
                content = source.read(MAX_READ_BYTES + 1)
                line_count = content.count(b'\n') + int(
                    bool(content) and not content.endswith(b'\n')
                )
                if len(content) <= MAX_READ_BYTES and line_count <= MAX_PAGE_LINES:
                    return _decode_read_text(content)
                source.seek(0)

            first_line = offset or 1
            page_limit = limit or MAX_PAGE_LINES
            line_number = 0
            while line_number < first_line - 1:
                chunk = source.readline(MAX_READ_BYTES + 1)
                if not chunk:
                    break
                while chunk and not chunk.endswith(b'\n'):
                    chunk = source.readline(MAX_READ_BYTES + 1)
                line_number += 1

            lines: list[str] = []
            page_bytes = 0
            has_more = False
            if line_number == first_line - 1:
                while len(lines) < page_limit:
                    chunk = source.readline(MAX_READ_BYTES + 1)
                    if not chunk:
                        break
                    if len(chunk) > MAX_READ_BYTES:
                        raise ValueError('line_too_long_for_page')
                    if page_bytes + len(chunk) > MAX_READ_BYTES:
                        has_more = True
                        break
                    lines.append(_decode_read_text(chunk))
                    page_bytes += len(chunk)
                    line_number += 1
                if len(lines) == page_limit:
                    has_more = bool(source.read(1))

        if not lines:
            result = f'[read: no lines from offset={first_line}; next_offset=none]'
        else:
            next_offset = str(line_number + 1) if has_more else 'none'
            result = (''.join(lines) + ('' if lines[-1].endswith('\n') else '\n')
                      + f'[read: lines {first_line}-{line_number}; '
                        f'next_offset={next_offset}]')
        return result
    except UnicodeError as error:
        logger.warning('read_failed error_type=%s', type(error).__name__)
        raise ToolException(INVALID_ENCODING) from error
    except ValueError as error:
        message = (NON_TEXT_BYTES if str(error) == 'non_text_content'
                   else LINE_TOO_LONG if str(error) == 'line_too_long_for_page'
                   else READ_OUTSIDE if str(error) == 'read_approval_missing_or_changed'
                   else READ_POLICY_DENIED if str(error) == 'policy_denied'
                   else READ_POLICY_UNAVAILABLE if str(error) == 'policy_unavailable'
                   else READ_UNAVAILABLE)
        logger.warning('read_failed error_type=%s', type(error).__name__)
        raise ToolException(message) from error
    except OSError as error:
        logger.warning('read_failed error_type=%s', type(error).__name__)
        raise ToolException(READ_IO_FAILURE) from error
    except GraphBubbleUp:
        raise
    except Exception as error:
        logger.warning('read_failed error_type=%s', type(error).__name__)
        raise ToolException(READ_TOOL_FAILURE) from error


def _safe_read_error(error: ToolException) -> str:
    message = error.args[0] if error.args else None
    return message if isinstance(message, str) and message in SAFE_READ_ERRORS else READ_TOOL_FAILURE


def _set_safe_tool_errors(read):
    read.handle_validation_error = INVALID_ARGS
    read.handle_tool_error = _safe_read_error
    return read


def create_read_tool(workspace_root: str | Path):
    """Create one read-only tool bound to a canonical, existing workspace."""
    root = resolve_workspace_root(workspace_root)

    @tool('read', args_schema=ReadInput)
    def read(path: str, offset: int | None = None, limit: int | None = None) -> str:
        """Read workspace UTF-8 text. Use 1-based offset/limit to continue by line.

        Pages follow the current file, so line positions may shift if it changes.
        """
        return _read(root, path, offset, limit)

    return _set_safe_tool_errors(read)


def create_session_read_tool():
    """Create a read tool whose root comes from the trusted run context."""

    @tool('read', args_schema=SessionReadInput)
    def read(path: str, runtime: ToolRuntime[dict, dict],
                  offset: int | None = None, limit: int | None = None) -> str:
        """Read session workspace UTF-8 text, with optional 1-based line pagination."""
        context = runtime.context
        root = context.get('workspace_root') if isinstance(context, dict) else None
        if not isinstance(root, Path) or not root.is_absolute():
            logger.warning('read_failed error_type=MissingWorkspaceContext')
            raise ToolException(READ_NO_WORKSPACE)
        grants = context.get('approval_grants', {}).get('read')
        if not isinstance(grants, ReadOnlyApprovalGrants):
            grants = None
        return _read(root, path, offset, limit, grants, runtime.tool_call_id,
                     context.get('read_resource_roots', ()),
                     access=_resolve_read_access(read, context, path))

    read.metadata = {**(read.metadata or {}), FILE_OPERATION_KEY: 'read'}
    return attach_approval(_set_safe_tool_errors(read), ContextualApprovalPolicy(
        when=lambda request: _read_policy_requires_approval(read, request),
        description='Review access to the requested file under the session read policy',
        review=lambda root, args: _review_read_policy(read, root, args, {}),
        grant_factory=ReadOnlyApprovalGrants,
        review_with_context=lambda root, args, context: _review_read_policy(read, root, args, context),
    ))
