"""Pass application-owned read scopes through ordinary and resumed invocation."""

from pathlib import Path

from langgraph.types import Command

from .async_invocation import ainvoke_agent_run


def _check_resources(workspace_root, read_resource_roots):
    if workspace_root is None and read_resource_roots == ():
        return
    if (not isinstance(workspace_root, Path) or not workspace_root.is_absolute()
            or '..' in workspace_root.parts):
        raise ValueError('A canonical workspace_root is required')
    if not isinstance(read_resource_roots, tuple):
        raise ValueError('Read resource roots must be a tuple')
    for root in read_resource_roots:
        # These are canonical discovery snapshots. Files may disappear before
        # invocation; Read revalidates the filesystem and returns a ToolMessage.
        if not isinstance(root, Path) or not root.is_absolute() or '..' in root.parts:
            raise ValueError('A canonical resource directory is required')
        root.relative_to(workspace_root)


async def ainvoke_agent_run_with_resources(agent, messages, *, session_id, thread_id, run_id,
                                         workspace_root, read_resource_roots, max_concurrency=4, durability=None):
    _check_resources(workspace_root, read_resource_roots)
    return await ainvoke_agent_run(agent, messages, session_id=session_id, thread_id=thread_id, run_id=run_id,
        workspace_root=workspace_root, read_resource_roots=read_resource_roots, max_concurrency=max_concurrency,
        **({'durability': durability} if durability is not None else {}))


async def aresume_agent_run_with_resources(agent, *, session_id, thread_id, run_id, workspace_root,
                                         read_resource_roots, decisions, approval_grants, max_concurrency=4, durability=None):
    _check_resources(workspace_root, read_resource_roots)
    return await ainvoke_agent_run(agent, Command(resume={'decisions': [{'type': item} for item in decisions]}),
        session_id=session_id, thread_id=thread_id, run_id=run_id, workspace_root=workspace_root,
        read_resource_roots=read_resource_roots, approval_grants=approval_grants, max_concurrency=max_concurrency,
        **({'durability': durability} if durability is not None else {}))
