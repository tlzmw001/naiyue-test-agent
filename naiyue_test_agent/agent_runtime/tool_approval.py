"""Tool-owned approval descriptions consumed by application and agent runtime."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


APPROVAL_METADATA_KEY = 'naiyue_approval'


class OneUseApprovalGrants:
    """Consume one exact tool-call grant, including its reviewed subject, once."""

    def __init__(self, approved: dict[str, object]):
        self._approved = dict(approved)
        self._lock = Lock()

    def consume(self, tool_call_id: str | None, *subject: object) -> bool:
        if not tool_call_id or not subject:
            return False
        expected = subject[0] if len(subject) == 1 else subject
        with self._lock:
            if tool_call_id not in self._approved or self._approved[tool_call_id] != expected:
                return False
            del self._approved[tool_call_id]
            return True


@dataclass(frozen=True)
class ApprovalReview:
    target: Path | str
    grant_value: object
    workspace: Path | None = None


@dataclass(frozen=True)
class ApprovalPolicy:
    when: Callable[[Any], bool]
    description: str
    review: Callable[[Path, dict], ApprovalReview | None]
    grant_factory: Callable[[dict[str, object]], OneUseApprovalGrants]


@dataclass(frozen=True)
class ContextualApprovalPolicy(ApprovalPolicy):
    """An additive descriptor for reviews needing trusted invocation context."""

    review_with_context: Callable[[Path, dict, dict], ApprovalReview | None]


def attach_approval(tool, policy: ApprovalPolicy):
    """Register one tool's approval contract without changing its model schema."""
    if not isinstance(policy, ApprovalPolicy):
        raise ValueError('Invalid tool approval policy')
    tool.metadata = {**(tool.metadata or {}), APPROVAL_METADATA_KEY: policy}
    return tool


def approval_policies_for_tools(tools: list) -> dict[str, ApprovalPolicy]:
    """Reject malformed or duplicate registrations before creating the agent."""
    policies = {}
    seen = set()
    for item in tools:
        name = item.name
        if name in seen:
            raise ValueError('Duplicate tool name in approval registration')
        seen.add(name)
        metadata = item.metadata or {}
        if APPROVAL_METADATA_KEY not in metadata:
            continue
        policy = metadata[APPROVAL_METADATA_KEY]
        if (not isinstance(policy, ApprovalPolicy) or not callable(policy.when)
                or not callable(policy.review) or not callable(policy.grant_factory)
                or not isinstance(policy.description, str) or not policy.description.strip()):
            raise ValueError('Invalid tool approval policy')
        if isinstance(policy, ContextualApprovalPolicy) and not callable(policy.review_with_context):
            raise ValueError('Invalid contextual approval review')
        policies[name] = policy
    return policies


def review_path(root: Path, args: dict, classifier: Callable,
                *, default: str | None = None) -> ApprovalReview | None:
    """Review one path using the same classifier the tool uses at execution."""
    if not isinstance(args, dict):
        return None
    path = args.get('path', default)
    if not isinstance(path, str):
        return None
    decision, target = classifier(root, path)
    return ApprovalReview(target=target, grant_value=target) if decision == 'ask' else None
