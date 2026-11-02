"""The shared required persistence/graph binding for local Hosts and fixed Runners."""
from .session_persistence import SessionPersistence


def assemble_session_runtime(sessions,storage,checkpointer,*,catalog,registry,permissions,
                             system_prompt,response_format,subagents=(),subagent_settings=None,**options):
    persistence=SessionPersistence(storage,sessions,catalog=catalog,tool_registry=registry,
        permission_settings=permissions,**options)
    sessions.bind_persistence(persistence)
    agent=sessions.assemble_runtime(checkpointer,system_prompt=system_prompt,response_format=response_format,
        subagents=subagents,subagent_settings=subagent_settings)
    return persistence,agent
