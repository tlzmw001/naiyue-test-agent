"""One read-only SDK stream diagnosis; compare raw tool data with pinned conversion."""

import json

from langchain_core.messages import HumanMessage
from langchain_openai.chat_models.base import _convert_responses_chunk_to_generation_chunk

from .provider_acceptance_cases import require


async def multicall_diagnostic(env):
    async with env.open_app(['subagent']) as app:
        model = app.host.sessions._sessions._catalog.get(env.model_id)
        tool = app.host.tools.list()[0].tool
        bound = model.bind_tools([tool])
        payload = model._get_request_payload([HumanMessage(content='Propose exactly two calls to subagent in '
            'one response: first agent="tester", task="LEFT"; second agent="tester", task="RIGHT". '
            'Use these exact string arguments, do not add fields or answer in text. Tools will not be executed.')],
            **bound.kwargs, stream=True)
        stream = await model.root_async_client.responses.create(**payload)
        state, joined, raw = (-1, -1, -1), None, {}
        trace, partial = [], {}
        allowed_types = {'response.created', 'response.in_progress', 'response.completed',
            'response.incomplete', 'response.failed', 'error', 'response.output_item.added',
            'response.output_item.done', 'response.function_call_arguments.delta',
            'response.function_call_arguments.done', 'response.output_text.delta',
            'response.output_text.done', 'response.content_part.added', 'response.content_part.done',
            'response.reasoning_summary_part.added', 'response.reasoning_summary_part.done',
            'response.reasoning_summary_text.delta', 'response.reasoning_summary_text.done'}
        env.diagnostics = {'events': trace}
        try:
            async for event in stream:
                index = getattr(event, 'output_index', None)
                row = {'type': event.type if event.type in allowed_types else 'other'}
                if type(index) is int:
                    row['output_index'] = index
                if event.type == 'response.output_item.added' and event.item.type == 'function_call':
                    partial[index] = event.item.arguments
                    row['initial_argument_chars'] = len(event.item.arguments)
                if event.type == 'response.function_call_arguments.delta':
                    partial[index] = partial.get(index, '') + event.delta
                    row['delta_chars'] = len(event.delta)
                if event.type == 'response.function_call_arguments.done':
                    row.update(final_argument_chars=len(event.arguments),
                        matches_accumulated=partial.get(index, '') == event.arguments)
                if event.type == 'response.output_item.done' and event.item.type == 'function_call':
                    raw[event.item.call_id] = event.item.arguments
                    row.update(final_argument_chars=len(event.item.arguments),
                        matches_accumulated=partial.get(index, '') == event.item.arguments)
                require(len(trace) < 512, 'diagnostic_event_budget')
                trace.append(row)
                *state, chunk = _convert_responses_chunk_to_generation_chunk(event, *state)
                if chunk is not None:
                    joined = chunk if joined is None else joined + chunk
        finally:
            await stream.close()
        calls = joined.message.tool_calls if joined is not None else []
        expected = [{'agent': 'tester', 'task': 'LEFT'}, {'agent': 'tester', 'task': 'RIGHT'}]
        rows = []
        for call in calls:
            try:
                native = json.loads(raw.get(call['id'], 'null'))
            except (TypeError, ValueError):
                native = None
            args = call['args']
            rows.append({'native_object': isinstance(native, dict), 'lc_equals_native': args == native,
                'keys': sorted(key if key in ('agent', 'task') else 'other' for key in args),
                'agent_string': isinstance(args.get('agent'), str), 'task_string': isinstance(args.get('task'), str)})
        env.diagnostics = {'events': trace, 'native_calls': len(raw), 'lc_calls': len(calls), 'arguments': rows,
                           'expected_arguments': [call['args'] for call in calls] == expected}
        require(len(raw) == len(calls) == 2, 'diagnostic_two_calls')
        require(all(row['lc_equals_native'] for row in rows), 'diagnostic_lc_matches_native')
    return {'read_only_diagnostic': True, **env.diagnostics}


async def nonstream_tool_diagnostic(env):
    async with env.open_app(['subagent']) as app:
        model = app.host.sessions._sessions._catalog.get(env.model_id).model_copy(update={'disable_streaming': 'tool_calling'})
        tool = app.host.tools.list()[0].tool
        bound = model.bind_tools([tool])
        messages = [part async for part in bound.astream([HumanMessage(content='Propose exactly two calls '
            'to subagent in one response: first agent="tester", task="LEFT"; second agent="tester", '
            'task="RIGHT". Use these exact string arguments and do not answer in text. '
            'Tools will not be executed.')])]
        require(len(messages) == 1, 'framework_nonstream_one_result')
        calls = messages[0].tool_calls
        expected = [{'agent': 'tester', 'task': 'LEFT'}, {'agent': 'tester', 'task': 'RIGHT'}]
        env.diagnostics = {'tool_calls': len(calls), 'invalid_calls': len(messages[0].invalid_tool_calls),
                           'expected_arguments': [call['args'] for call in calls] == expected}
        require(len(calls) == 2 and env.diagnostics['expected_arguments'], 'nonstream_tool_arguments')
    return {'read_only_diagnostic': True, 'framework_opt_out': 'tool_calling', **env.diagnostics}
