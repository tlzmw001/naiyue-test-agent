"""Small trusted file-policy contract, independent of UI and tool IO."""

import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from langgraph.errors import GraphBubbleUp


logger = logging.getLogger(__name__)
FILE_PERMISSION_KEY = 'naiyue_file_permission_policy'
FILE_OPERATION_KEY = 'naiyue_file_permission_operation'


@dataclass(frozen=True)
class FileAccessRequest:
    tool_name: str
    operation: str
    workspace_root: Path
    target: Path
    session_id: str | None = None


@dataclass(frozen=True)
class FileAccessDecision:
    kind: Literal['allow', 'ask', 'deny']
    reason: Literal['policy_denied', 'policy_unavailable'] | None = None

    def __post_init__(self):
        if self.kind not in ('allow', 'ask', 'deny'):
            raise ValueError('Invalid permission decision')
        if self.reason not in (None, 'policy_denied', 'policy_unavailable'):
            raise ValueError('Invalid permission reason')
        if self.kind != 'deny' and self.reason is not None:
            raise ValueError('Only a denial can have a failure reason')


def attach_file_permission_policy(tool, policy):
    """Install a local synchronous decision callback on a declared tool."""
    metadata = tool.metadata or {}
    if metadata.get(FILE_OPERATION_KEY) != 'read':
        raise ValueError('Tool does not declare file-policy support')
    if (not callable(policy) or inspect.iscoroutinefunction(policy)
            or inspect.iscoroutinefunction(getattr(policy, '__call__', None))):
        raise ValueError('A synchronous local permission callback is required')
    tool.metadata = {**metadata, FILE_PERMISSION_KEY: policy}
    return tool


def decide_file_access(tool, request, default_kind):
    """The caller validates/canonicalizes the target before reaching this seam."""
    policy = (tool.metadata or {}).get(FILE_PERMISSION_KEY)
    if policy is None:
        return FileAccessDecision(default_kind)
    try:
        if not callable(policy):
            raise TypeError('Invalid permission callback')
        decision = policy(request)
        if inspect.iscoroutine(decision):
            decision.close()
            raise TypeError('Async result is not supported by the local permission callback')
        if not isinstance(decision, FileAccessDecision):
            raise TypeError('Invalid permission callback result')
        return (FileAccessDecision('deny', decision.reason or 'policy_denied')
                if decision.kind == 'deny' else decision)
    except GraphBubbleUp:
        raise
    except Exception as error:
        logger.error('file_permission_policy_failed error_type=%s', type(error).__name__)
        return FileAccessDecision('deny', 'policy_unavailable')
