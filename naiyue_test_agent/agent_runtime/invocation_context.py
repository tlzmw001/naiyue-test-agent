"""Reuse the run scope and allocate fresh state for every graph invoke/resume."""

from ..contracts.run_context import RunIdentity, RunScope, RuntimeContext, InvocationInputs
from ..execution_roots import ExecutionRoot
from .run_scope import current_run_scope


def make_invocation_context(session_id, run_id, workspace_root, approval_grants, read_resource_roots, *, agent_path='root'):
    scope = current_run_scope()
    if scope is None:
        scope = RunScope(RunIdentity(session_id, run_id, agent_path,
            execution_root=ExecutionRoot(workspace_root) if workspace_root is not None else None))
    identity = scope.identity
    if (identity.session_id, identity.run_id, identity.agent_path, identity.execution_path) != (
            session_id, run_id, agent_path, workspace_root):
        raise ValueError('Run scope identity does not match invocation')
    publisher = scope.observation.publisher
    if publisher is not None and not publisher.matches(session_id, run_id):
        raise ValueError('Run scope publisher does not match identity')
    return RuntimeContext(scope, inputs=InvocationInputs(
        approval_grants=approval_grants, read_resource_roots=read_resource_roots))
