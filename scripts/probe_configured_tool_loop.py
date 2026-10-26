"""Probe a configured model with synthetic model → tool → model data."""

import asyncio
from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

import argparse

from naiyue_test_agent.config_paths import get_models_path
import os
import secrets
import signal


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--model-id', required=True)
    args = parser.parse_args()

    # The probe must not inherit hosted tracing settings or upload its messages.
    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    def on_timeout(_signum, _frame):
        raise TimeoutError('Configured probe deadline exceeded')

    signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(90)

    from langchain.tools import tool
    if __package__:
        from .component_fixtures import build_component_application
    else:
        from component_fixtures import build_component_application
    from scripts.provider_acceptance_budget import RequestBudget, guard_application_catalog

    expected = secrets.token_hex(6)
    executions = []
    budget = RequestBudget(2)

    @tool
    def read_probe_value() -> str:
        """Return the private diagnostic value for this run."""
        executions.append(1)
        if len(executions) > 1:
            raise RuntimeError('Diagnostic tool called more than once')
        return expected

    try:
        async with guard_application_catalog(budget):
            app = await build_component_application(args.config,
                tools=[ToolSpec(read_probe_value, read_probe_value.args_schema,
                                permissions=ToolPermission(default='allow'))],
                system_prompt='Call the requested tool, then return exactly its result.', response_format=None)
            try:
                session = await app.host.sessions.create(args.model_id)
                result = await app.host.runs.start(session, [('user',
                    'Call read_probe_value exactly once, then reply only with the exact value it returns.')])
            finally:
                await app.aclose()
        messages = result['messages']
        calls = [call for message in messages if message.type == 'ai'
                 for call in message.tool_calls]
        tool_messages = [message for message in messages if message.type == 'tool']
        if not (len(executions) == len(calls) == len(tool_messages) == 1
                and calls[0]['id'] == tool_messages[0].tool_call_id
                and messages[-1].content.strip() == expected and budget.snapshot()['used'] == 2):
            raise AssertionError('Configured tool-loop contract failed')
    except Exception as exc:
        print(f'status=FAIL error_type={type(exc).__name__}')
        return 1
    else:
        print('status=PASS model_calls=2 tool_executions=1 final_matches_tool_result=true')
        return 0
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
