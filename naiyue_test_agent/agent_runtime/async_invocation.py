"""Async run entry uses the same graph, correlation and trusted context."""

import logging

from langgraph.types import Command

from .invocation_context import make_invocation_context
from .async_message_observation import ainvoke_with_message_observation


logger = logging.getLogger('naiyue_test_agent.agent_runtime')


async def ainvoke_agent_run(agent, messages, *, session_id, thread_id, run_id,
                           workspace_root=None, approval_grants=None, read_resource_roots=(), max_concurrency=4):
    for name, value in (('session_id', session_id), ('thread_id', thread_id), ('run_id', run_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'A non-empty {name} is required')
    correlation = {'run_id': run_id, 'session_id': session_id}
    logger.info('run_started', extra={'run_event': {'event': 'run_started', **correlation}})
    context = make_invocation_context(session_id, run_id, workspace_root, approval_grants, read_resource_roots)
    input = messages if isinstance(messages, Command) else {'messages': messages}
    config = {'configurable': {'thread_id': thread_id}, 'max_concurrency': max_concurrency}
    try:
        publisher = context.get('_run_event_publisher')
        if publisher is not None:
            result = await ainvoke_with_message_observation(agent, input, context=context, config=config, publisher=publisher)
        else:
            result = await agent.ainvoke(input, context=context, config=config)
    except BaseException:
        logger.error('run_failed', extra={'run_event': {'event': 'run_failed', **correlation}})
        raise
    terminal = ('run_interrupted' if isinstance(result, dict) and result.get('__interrupt__')
                else 'run_stopped' if isinstance(result, dict) and result.get('run_stopped') is True else 'run_completed')
    logger.info(terminal, extra={'run_event': {'event': terminal, **correlation}})
    return result
