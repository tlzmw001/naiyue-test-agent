"""Formal typed fixtures; constructor groups match the production contracts."""

from naiyue_test_agent.contracts.run_context import (
    RunIdentity, RunScope, RunCapabilities, RunObservationPorts, RuntimeContext,
    InvocationInputs, InvocationState, ModelCallState, PreparedRequest, ToolAuthorization,
)
from naiyue_test_agent.execution_roots import ExecutionRoot
from naiyue_test_agent.execution.host_files import HostFileSystem
from naiyue_test_agent.execution.host_processes import HostProcesses
from dataclasses import replace


def host_files(path):
    return HostFileSystem(ExecutionRoot(path), None)


def file_fixture(identity):
    if identity.execution_root is None:
        return identity, None
    # The tool feedback harness uses this owner when no session was specified.
    identity = replace(identity, session_id=identity.session_id or 'feedback')
    return identity, HostFileSystem(identity.execution_root, identity.project_id, session_id=identity.session_id)


def identity_for(publisher):
    source = getattr(publisher, 'source', None)
    root = publisher._root if source is not None else publisher
    return RunIdentity(root._session_id, publisher.run_id,
        source.agent_path if source is not None else 'root')


def typed_context(*, identity=None, capabilities=None, observation=None, control=None, errors=None,
                  archive_root=None, background_notices=None, inputs=None, authorization=None,
                  model_call=None, tool_batch=None, child_model_failure=None):
    identity = identity if identity is not None else RunIdentity(None)
    if capabilities is None:
        identity, files = file_fixture(identity)
        capabilities = RunCapabilities(files=files, processes=HostProcesses(files) if files is not None else None)
    return RuntimeContext(RunScope(identity,
        environment=capabilities.files.environment if capabilities.files is not None else None,
        capabilities=capabilities, observation=observation or RunObservationPorts(),
        control=control, errors=errors, archive_root=archive_root, background_notices=background_notices),
        invocation=InvocationState(model_call, tool_batch, child_model_failure),
        inputs=inputs or InvocationInputs(), authorization=authorization)


def typed_scope(*, identity=None, publisher=None, control=None, errors=None, turns=None,
                controls=None, context_operations=None, tool_progress=None, invoke_subagent=None,
                list_subagents=None, process_tasks=None, task_queries=None, history=None, memory=None,
                archive_root=None, background_notices=None):
    if identity is None:
        identity = identity_for(publisher) if publisher is not None else RunIdentity(None)
    identity, files = file_fixture(identity)
    return RunScope(identity, RunCapabilities(invoke_subagent, list_subagents, process_tasks, task_queries, history, memory, files, HostProcesses(files) if files is not None else None),
        RunObservationPorts(publisher, turns, controls, context_operations, tool_progress), control, errors,
        archive_root, background_notices, files.environment if files is not None else None)
