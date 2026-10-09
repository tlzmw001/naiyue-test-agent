"""Probe a configured model with synthetic model → tool → model data."""

import argparse
import os
import secrets
import signal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='.env/models.json')
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
    from naiyue_test_agent.agent_runtime import build_configured_runtime_agent

    expected = secrets.token_hex(6)
    executions = []
    selections = []

    def record_selection(session_id: str, model_id: str) -> None:
        if len(selections) >= 2:
            raise RuntimeError('Configured probe model-call limit reached')
        selections.append((session_id, model_id))

    @tool
    def read_probe_value() -> str:
        """Return the private diagnostic value for this run."""
        executions.append(1)
        if len(executions) > 1:
            raise RuntimeError('Diagnostic tool called more than once')
        return expected

    try:
        agent = build_configured_runtime_agent(
            args.config,
            tools=[read_probe_value],
            system_prompt='Use the requested diagnostic tool and return its exact value.',
            response_format=None,
            select_model_id=lambda _session_id: args.model_id,
            record_selection=record_selection,
        )
        result = agent.invoke(
            {'messages': [('user',
                           'Call read_probe_value exactly once. Reply only with the value returned by the tool.')]},
            context={'session_id': 'configured-probe'},
            config={'recursion_limit': 6},
        )
        messages = result['messages']
        calls = [call for message in messages if message.type == 'ai'
                 for call in message.tool_calls]
        tool_messages = [message for message in messages if message.type == 'tool']
        if not (len(executions) == len(calls) == len(tool_messages) == 1
                and calls[0]['id'] == tool_messages[0].tool_call_id
                and messages[-1].content.strip() == expected and len(selections) == 2):
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
    raise SystemExit(main())
