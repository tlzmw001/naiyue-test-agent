"""Bounded real model/tool check for complete message subscriptions."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from scripts.provider_acceptance_budget import RequestBudget, guard_application_catalog

import argparse
import asyncio
from contextlib import AsyncExitStack

from naiyue_test_agent.config_paths import get_models_path
import json
import logging
import os
import secrets
import subprocess
import sys
from unittest.mock import patch


def _worker(args):
    return asyncio.run(_async_worker(args))


async def _async_worker(args):
    async with AsyncExitStack() as resources:
        os.environ['LANGSMITH_TRACING'] = 'false'
        os.environ['LANGCHAIN_TRACING_V2'] = 'false'
        logging.getLogger().setLevel(logging.CRITICAL)
        from langchain.tools import tool
        from langchain_core.messages import AIMessage, ToolMessage
        from naiyue_test_agent.application.async_application import build_async_local_application
        from naiyue_test_agent.cli.cli_async import AsyncCliApplication

        expected = secrets.token_hex(6)
        executions, events = [], []
        budget = RequestBudget(2)
        holder = {}
        with_control = getattr(args, 'with_control', False)
        with_cli = getattr(args, 'with_cli', False)
        displayed = []
        @tool
        def read_probe_value() -> str:
            """Return the diagnostic value generated for this run."""
            if executions:
                raise RuntimeError('Probe tool-call limit reached')
            if with_cli:
                assert any(line.strip() == 'CLI_BEFORE_TOOL' for line in displayed)
            executions.append(True)
            if with_control:
                text = ('The requested tool has returned. Do not call more tools. '
                    'Reply with only the exact value returned by read_probe_value.')
                if with_cli:
                    holder['app'].call(lambda api: api.runs.steer(holder['session'], text))
                else:
                    holder['app'].runs.steer(holder['session'], text)
            return expected

        async with guard_application_catalog(budget):
            options = dict(tools=[ToolSpec(read_probe_value, read_probe_value.args_schema,
                permissions=ToolPermission(default='allow'))],
                system_prompt='Call the requested tool and return its exact value.', response_format=None)
            if with_cli:
                # The CLI bridge owns another loop; this caller never runs on it.
                app = AsyncCliApplication(args.config, **options)
                resources.callback(app.close)
                session = app.call(lambda api: api.sessions.create(args.model_id or api.models.default_id))
            else:
                app = await build_async_local_application(args.config, **options)
                resources.push_async_callback(app.aclose)
                session = await app.sessions.create(args.model_id or app.models.default_id)
            if with_cli:
                app.call(lambda api: api.observability.subscribe(events.append))
            else:
                app.observability.subscribe(events.append)
            holder.update(app=app, session=session)
            prompt = 'Call read_probe_value exactly once. Reply only with the exact value returned by it.'
            if with_cli:
                from naiyue_test_agent.cli.chat import _run_and_print
                from naiyue_test_agent.cli.cli_display import CliEventDisplay
                view = CliEventDisplay(session, writer=displayed.append)
                unsubscribe = app.call(lambda api: api.observability.subscribe(view))
                original_call = app.call

                def capture_result(action):
                    value = original_call(action)
                    if isinstance(value, dict) and 'messages' in value:
                        holder['result'] = value
                    return value

                try:
                    with patch.object(app, 'call', capture_result):
                        _run_and_print(app, session,
                            'In the assistant message containing the tool call, write exactly CLI_BEFORE_TOOL as text. '
                            + prompt, display=view)
                finally:
                    unsubscribe()
                result = holder['result']
                assert sum(line.strip() == 'CLI_BEFORE_TOOL' for line in displayed) == 1
                assert sum(line.strip() == expected for line in displayed) == 1
                assert displayed.count('本次运行完成') == 1
                assert '工具 read_probe_value：处理完成' in displayed
            else:
                result = await app.runs.start(session, [('user', prompt)])
        messages = result['messages']
        snapshots = [e.data for e in events if e.event_type == 'message_completed']
        rows = [json.loads(data.snapshot_json) for data in snapshots]
        assert budget.snapshot()['used'] == 2 and len(executions) == 1
        assert [row['type'] for row in rows] == (
            ['human', 'ai', 'tool', 'human', 'ai'] if with_control else ['human', 'ai', 'tool', 'ai'])
        assert [row['id'] for row in rows] == [m.id for m in messages]
        assert [row['content'] for row in rows] == [m.content for m in messages]
        calls = [c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
        tools = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(calls) == len(tools) == 1
        assert rows[1]['tool_calls'][0]['id'] == rows[2]['tool_call_id'] == calls[0]['id'] == tools[0].tool_call_id
        assert rows[2]['content'] == expected and tools[0].status == 'success'
        assert messages[-1].content.strip() == expected
        assert all(data.visibility == 'graph_values' for data in snapshots)
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'completed'
        starts = [e for e in events if e.event_type == 'turn_started']
        ends = [e for e in events if e.event_type == 'turn_finished']
        assert len(starts) == len(ends) == 2
        assert [e.data.turn_id for e in starts] == [e.data.turn_id for e in ends]
        assert starts[0].data.turn_id != starts[1].data.turn_id
        assert all(e.data.status == 'completed' for e in ends)
        assert ends[0].data.assistant_message_id == messages[1].id
        assert ends[0].data.tool_message_ids == (messages[2].id,)
        assert ends[1].data.assistant_message_id == messages[-1].id
        assert starts[0].sequence < ends[0].sequence < starts[1].sequence < ends[1].sequence
        if with_control:
            controls = [e for e in events if e.event_type.startswith('control_')]
            assert [e.event_type for e in controls] == ['control_accepted', 'control_delivery_started', 'control_delivered']
            assert len({e.data.control_id for e in controls}) == 1
            assert controls[-1].data.message_id == messages[3].id
        control_text = 'controls=1 ' if with_control else ''
        display_text = 'cli_display=verified replies=2 ' if with_cli else ''
        print(f'real_message_events=PASS events={len(events)} messages={len(rows)} '
              f'model_calls=2 tool_calls=1 turns=2 {control_text}{display_text}pairing=verified terminal=completed', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--model-id')
    parser.add_argument('--with-control', action='store_true')
    parser.add_argument('--with-cli', action='store_true')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            _worker(args)
        except Exception as error:
            from naiyue_test_agent.agent_runtime.events import safe_error_type
            print(f'real_message_events=FAIL error_type={safe_error_type(error)}', flush=True)
            return 1
        return 0
    command = [sys.executable, '-m', 'scripts.probe_message_events', '--worker', '--config', args.config]
    if args.model_id:
        command.extend(['--model-id', args.model_id])
    if args.with_control:
        command.append('--with-control')
    if args.with_cli:
        command.append('--with-cli')
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=95)
    except subprocess.TimeoutExpired:
        print('real_message_events=FAIL reason=deadline_exceeded')
        return 1
    print(result.stdout.strip())
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
