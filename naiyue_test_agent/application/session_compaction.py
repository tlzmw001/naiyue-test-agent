"""Prepare session-owned inputs for the Runtime's manual checkpoint operation."""

from naiyue_test_agent.agent_runtime.context_usage import compose_model_prompt
from naiyue_test_agent.agent_runtime.manual_compaction import compact_checkpoint


async def compact_session(sessions, agent, session_id, thread_id, run_id):
    model_id, model = sessions.resolve_model(session_id)
    specs = sessions._tool_registry.select(sessions.get_active_tools(session_id))
    roles = [(role.name, role.description) for role in sessions.get_registered_subagents()] if any(
        spec.tool.name == 'subagent' for spec in specs) else ()
    system = compose_model_prompt(sessions._system_prompt, sessions.resolve_prompt_parts(session_id), specs, roles)
    return await compact_checkpoint(agent, session_id=session_id, thread_id=thread_id, run_id=run_id,
        model_id=model_id, model=model, system_message=system, tools=[spec.tool for spec in specs])
