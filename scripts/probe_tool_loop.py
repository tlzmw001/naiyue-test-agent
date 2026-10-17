"""Live connectivity probe; synthetic data only, not a business evaluation."""

import asyncio
import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import sys
import time


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--timeout', type=float, default=45)
    args = parser.parse_args()
    # This probe records locally; do not inherit hosted tracing settings.
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    key = ''
    events = []
    started = time.monotonic()
    try:
        entries = []
        for line in args.key_file.read_text().splitlines():
            name, separator, value = line.strip().removeprefix('export ').partition('=')
            if separator and name.strip() == 'OPENAI_API_KEY':
                parts = shlex.split(value, comments=True)
                if len(parts) != 1 or not parts[0]:
                    raise ValueError('Invalid OPENAI_API_KEY entry')
                entries.append(parts[0])
        if len(entries) != 1:
            raise ValueError('Expected one OPENAI_API_KEY entry in key file')
        key = entries[0]

        from langchain.agents import create_agent
        from langchain.agents.middleware import wrap_model_call, wrap_tool_call
        from langchain.messages import AIMessage, ToolMessage
        from langchain.tools import tool
        from langchain_openai import ChatOpenAI

        # Generated locally; the model can only discover it through the tool.
        probe_value = secrets.token_hex(8)
        model_inputs = []
        executions = []

        @tool
        def read_probe_value() -> str:
            """Read the local diagnostic value for this connectivity check."""
            executions.append(probe_value)
            return probe_value

        @wrap_model_call
        async def observe_model(request, handler):
            if len(model_inputs) >= 2:
                raise RuntimeError('Probe model-call limit reached')
            model_inputs.append(list(request.messages))
            events.append({'event': 'model_start', 'step': len(model_inputs),
                           'message_types': [m.type for m in request.messages],
                           'tool_result_ids': [m.tool_call_id for m in request.messages
                                               if isinstance(m, ToolMessage)]})
            response = await handler(request)
            events.append({'event': 'model_end', 'step': len(model_inputs)})
            return response

        @wrap_tool_call
        async def observe_tool(request, handler):
            call = request.tool_call
            events.append({'event': 'tool_start', 'name': call['name'],
                           'tool_call_id': call['id']})
            result = await handler(request)
            events.append({'event': 'tool_end', 'tool_call_id': call['id']})
            return result

        model = ChatOpenAI(
            model=args.model, base_url=args.base_url, api_key=key,
            use_responses_api=True, streaming=True, store=False,
            timeout=args.timeout, max_retries=0,
            model_kwargs={'instructions': 'Follow the diagnostic task exactly.'},
        )
        agent = create_agent(model, tools=[read_probe_value],
                             middleware=[observe_model, observe_tool])
        result = (await agent.ainvoke({'messages': [('user',
            'Call read_probe_value exactly once. Then reply with only the exact '
            'value returned by that tool. Do not guess the value.')]},
            config={'recursion_limit': 6}))
        messages = result['messages']
        calls = [call for msg in messages if isinstance(msg, AIMessage)
                 for call in msg.tool_calls]
        results = [msg for msg in messages if isinstance(msg, ToolMessage)]
        if len(executions) != 1 or len(calls) != 1 or len(results) != 1:
            raise AssertionError('Expected one actual tool execution and result')
        if results[0].tool_call_id != calls[0]['id']:
            raise AssertionError('Tool call/result ID mismatch')
        if len(model_inputs) != 2 or not any(
            isinstance(msg, ToolMessage)
            and msg.tool_call_id == calls[0]['id'] and msg.content == probe_value
            for msg in model_inputs[1]
        ):
            raise AssertionError('Tool result missing from second model input')
        if messages[-1].text.strip() != probe_value:
            raise AssertionError('Final response does not match actual tool result')
        report = {
            'check': 'create_agent_tool_loop', 'status': 'PASS',
            'requested_model': args.model,
            'returned_models': [m.response_metadata.get('model_name')
                                for m in messages if isinstance(m, AIMessage)],
            'model_calls': len(model_inputs), 'tool_executions': len(executions),
            'tool_result_in_second_input': True, 'final_matches_tool_result': True,
            'usage_per_call': [m.usage_metadata for m in messages
                               if isinstance(m, AIMessage)],
            'events': events,
            'elapsed_seconds': round(time.monotonic() - started, 2),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        detail = str(exc)
        if key:
            detail = detail.replace(key, '[REDACTED]')
        print(json.dumps({'check': 'create_agent_tool_loop', 'status': 'FAIL',
                          'error_type': type(exc).__name__,
                          'http_status': getattr(exc, 'status_code', None),
                          'detail': detail[:1200], 'events': events},
                         ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
