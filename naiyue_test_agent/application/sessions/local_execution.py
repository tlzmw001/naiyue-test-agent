"""Resolve the actual Host root for local sessions and current Runner sessions."""
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.execution_roots import ExecutionRootUnavailable, execution_error


def resolve_saved_execution(sessions,row):
    root=sessions.saved_execution_root(row)
    return sessions.resolve_execution_root(root,row['project_id'],row.get('execution_binding'))


def saved_execution_status(sessions,row):
    root=sessions.saved_execution_root(row)
    try:
        root=sessions.resolve_execution_root(root,row['project_id'],row.get('execution_binding'))
        return root,execution_error(root,row['project_id'])
    except ExecutionRootUnavailable as error:
        return root,error.public()


async def resolve_files(sessions,row):
    from naiyue_test_agent.execution.host_files import HostFileSystem
    root=await run_blocking(resolve_saved_execution,sessions,row)
    return HostFileSystem(root,row['project_id'],session_id=row['session_id']) if root is not None else None
