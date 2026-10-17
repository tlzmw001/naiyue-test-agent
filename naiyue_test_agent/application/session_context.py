"""Read-only context preparation; no graph update, summary, or model invocation."""

import logging
from pathlib import Path

from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.agent_runtime.context_usage import SNAPSHOT_KEY, compose_model_prompt, estimate_context, read_snapshot
from naiyue_test_agent.model_access.messages import prepare_model_messages
from .skills import prepare_run_resources


logger = logging.getLogger(__name__)


async def context_view(app, session_id):
    sessions = app._sessions
    config = None
    if app.has_session(session_id):
        with sessions._lock:
            row = dict(sessions._sessions[session_id])
        config = {'configurable': {'thread_id': row['thread_id']}}
        snapshot = await app._agent.aget_state(config)
        messages = snapshot.values.get('messages', [])
        available = (not row['pending_tool_approval'] and not row['manual_review_required']
            and not snapshot.next and not any(task.interrupts or task.error for task in snapshot.tasks))
    else:
        history = await sessions._persistence.ahistory(session_id, app._agent)
        row = {'model_id': history['model_id'], 'workspace_root': history['workspace_root'],
            'active_tool_names': history['active_tools'], 'busy': False}
        messages = history['messages']
        available = history['recovery']['kind'] == 'ready'
    latest = None
    for message in reversed(messages):
        if message.type == 'ai' and SNAPSHOT_KEY in message.response_metadata:
            latest = read_snapshot(message.response_metadata[SNAPSHOT_KEY])
            if latest is not None:
                break
    current = {'basis': 'unavailable', 'estimate': None}
    if row['busy']:
        current['basis'] = 'last_sent'
        return {'session_id': session_id, 'current': current, 'latest_request': latest}
    if available:
        try:
            model = app._catalog.get(row['model_id'])
            root = Path(row['workspace_root']) if row['workspace_root'] else None
            resources = await run_blocking(prepare_run_resources, root,
                enable_skills='read' in app._tool_registry.names())
            specs = app._tool_registry.select(row['active_tool_names'])
            roles = [(r.name, r.description) for r in sessions.get_registered_subagents()] if any(
                s.tool.name == 'subagent' for s in specs) else ()
            system = compose_model_prompt(sessions._system_prompt, resources['prompt_parts'], specs, roles)
            estimate = estimate_context(model, system, prepare_model_messages(messages, model), [s.tool for s in specs])
            current = {'basis': 'next_request', 'model_id': row['model_id'], 'estimate': estimate}
            if config is not None:
                after = await app._agent.aget_state(config)
                with sessions._lock:
                    now = sessions._sessions[session_id]
                    if (now['busy'] or now['model_id'] != row['model_id']
                            or tuple(now['active_tool_names']) != tuple(row['active_tool_names'])
                            or now['pending_tool_approval'] or now['manual_review_required']
                            or after.config != snapshot.config):
                        current = {'basis': 'unavailable', 'estimate': None}
        except Exception as error:
            logger.warning('context_preview_unavailable error_type=%s', type(error).__name__)
    return {'session_id': session_id, 'current': current, 'latest_request': latest}
