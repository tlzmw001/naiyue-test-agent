"""Bounded, one-request native async model probe; never prints model content."""

import argparse

from naiyue_test_agent.config_paths import get_models_path
import asyncio
import logging
import math
import subprocess
import sys
from uuid import uuid4
from types import SimpleNamespace


async def _worker(args):
    from langchain_core.callbacks import AsyncCallbackHandler
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from naiyue_test_agent.agent_runtime import build_runtime_agent, _assemble_interactive_runtime
    from naiyue_test_agent.tools import ToolRegistry
    from naiyue_test_agent.agent_runtime.context_compaction import SUMMARY_MARKER
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
    from naiyue_test_agent.model_access import load_model_catalog
    from scripts.provider_acceptance_budget import RequestBudget, guard_catalog
    from naiyue_test_agent.agent_runtime.tool_execution.permissions import fixed_permission_context

    from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback
    from naiyue_test_agent.agent_runtime.run_services import RunServices, bind_run_services
    catalog = load_model_catalog(args.config)
    install_model_usage_callback(catalog)
    try:
        budget = RequestBudget(2 if args.with_context else 1)
        guard_catalog(catalog, budget)
        model_id = args.model_id or catalog.default_model_id
        model = catalog.get(model_id)
        if type(model)._agenerate is BaseChatModel._agenerate:
            raise ValueError('The configured adapter does not provide native async generation')
        kinds = []

        class CountCalls(AsyncCallbackHandler):
            async def on_chat_model_start(self, serialized, messages, **kwargs):
                summary = str(messages[0][0].content).startswith('Summarize completed')
                kinds.append('summary' if summary else 'normal')

        events = []
        publisher = RunEventPublisher('async-probe', uuid4().hex, (events.append,))
        publisher.publish('run_started', RunStartedData('message'))
        def resolve_model(session_id, agent_path='root'):
            return model_id, model

        common = dict(tool_registry=ToolRegistry(), response_format=None, checkpointer=InMemorySaver())
        if args.with_context:
            metadata = dict(model.metadata or {})
            window = metadata['naiyue_model_limits']['context_window_tokens']
            if window <= 500:
                raise ValueError('Synthetic context probe requires a window above 500')
            metadata['naiyue_compaction_reserve'] = {'tokens': window - 500, 'source': 'probe'}
            metadata['naiyue_compaction_keep_recent'] = {'tokens': 16, 'source': 'probe'}
            # Only this process-local probe copy uses synthetic trigger/retention budgets.
            model = model.model_copy(update={'metadata': metadata})
            port = SimpleNamespace(
                resolve_model=resolve_model,
                resolve_request_configuration=lambda sid, path: (*resolve_model(sid, path), ()),
                resolve_active_tool_names=lambda _sid, agent_path='root': (),
                resolve_permission_context=fixed_permission_context,
                resolve_prompt_parts=lambda _, agent_path='root': [{'id': 'project',
                    'text': '\nUse the supplied synthetic history and summary.'}],
                get_registered_subagents=lambda: (),
                drain_steering=lambda _: [], is_stop_requested=lambda _: False,
                finish_run_boundary=lambda _: (False, []),
                prepare_control_messages=lambda _sid, messages: [HumanMessage(content=text) for text in messages])
            graph = _assemble_interactive_runtime(model, system_prompt='Answer the requested question precisely.',
                **common, session_port=port)
        else:
            graph = build_runtime_agent(model, system_prompt='', **common,
                resolve_model=resolve_model, resolve_active_tool_names=lambda _sid, agent_path='root': ())
        marker = uuid4().hex
        config = {'configurable': {'thread_id': 'async-probe'}, 'callbacks': [CountCalls()]}
        context = {'session_id': 'async-probe', 'run_id': publisher.run_id, '_run_event_publisher': publisher}
        inputs = [('user', f'Reply only with this exact value: {marker}')]
        if args.with_context:
            inputs = [HumanMessage(content=f'Remember this synthetic code: {marker}. '
                        + 'Archived synthetic context. ' * 400, id='probe-old-user'),
                      AIMessage(content='Recorded.', id='probe-old-ai'),
                      HumanMessage(content='Keep that code for the next question.'),
                      AIMessage(content='Acknowledged.'),
                      HumanMessage(content='Reply only with the synthetic code from earlier conversation.')]
        with bind_run_services(RunServices(publisher=publisher)):
            async with asyncio.timeout(args.timeout_seconds):
                if args.with_context:
                    result, summary_visible = None, False
                    async for part in graph.astream({'messages': inputs}, context=context, config=config,
                                                   stream_mode=['updates', 'values'], version='v2'):
                        if part['type'] == 'values':
                            result = part['data']
                            summary_visible |= bool(result['messages'][0].additional_kwargs.get(SUMMARY_MARKER))
                    assert summary_visible
                else:
                    result = await graph.ainvoke({'messages': inputs}, context=context, config=config)
        messages = result['messages']
        assert isinstance(messages[-1], AIMessage)
        if args.with_context:
            assert kinds == ['summary', 'normal']
            assert messages[0].additional_kwargs.get(SUMMARY_MARKER) is True
            assert not any(m.id == 'probe-old-user' for m in messages)
        else:
            assert len(messages) == 2 and kinds == ['normal']
        assert messages[-1].content.strip() == marker and not messages[-1].tool_calls
        starts = [event for event in events if event.event_type == 'model_call_started']
        ends = [event for event in events if event.event_type == 'model_call_finished']
        assert [event.data.model_id for event in starts] == [model_id] * len(kinds)
        assert [event.data.invocation_id for event in starts] == [event.data.invocation_id for event in ends]
        assert all(event.data.status == 'returned' for event in ends)
        snapshots = [event for event in events if event.event_type == 'model_context_snapshot']
        assert len(snapshots) == 2
        assert snapshots[0].data.model_call_id == snapshots[1].data.model_call_id
        assert snapshots[0].data.model_call_id != starts[-1].data.invocation_id
        assert budget.snapshot()['used'] == (2 if args.with_context else 1)
        assert not any(event.event_type.startswith('model_invocation_') for event in events)
        snapshot = await graph.aget_state(config)
        assert [m.id for m in snapshot.values['messages']] == [m.id for m in messages]
        label = 'async_context_probe' if args.with_context else 'async_model_probe'
        stream_fact = 'state_stream=verified ' if args.with_context else ''
        print(f'{label}=PASS logical_calls={len(kinds)} native_adapter=True messages={len(messages)} '
              f'{stream_fact}model_event_pairing=verified memory_checkpoint=verified', flush=True)
    finally:
        await catalog.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--model-id')
    parser.add_argument('--timeout-seconds', type=float, default=90)
    parser.add_argument('--with-context', action='store_true')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error('--timeout-seconds must be positive and finite')
    if args.worker:
        logging.disable(logging.CRITICAL)
        try:
            asyncio.run(_worker(args))
        except Exception as error:
            from naiyue_test_agent.agent_runtime.events import safe_error_type
            label = 'async_context_probe' if args.with_context else 'async_model_probe'
            print(f'{label}=FAIL error_type={safe_error_type(error)}', flush=True)
            return 1
        return 0
    command = [sys.executable, '-m', 'scripts.probe_async_model', '--worker',
               '--config', args.config, '--timeout-seconds', str(args.timeout_seconds)]
    if args.model_id:
        command.extend(['--model-id', args.model_id])
    if args.with_context:
        command.append('--with-context')
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=args.timeout_seconds + 5)
    except subprocess.TimeoutExpired:
        print('async_model_probe=FAIL reason=deadline_exceeded')
        return 1
    output = result.stdout.strip()
    if output.startswith(('async_model_probe=', 'async_context_probe=')):
        print(output)
    else:
        print('async_model_probe=FAIL reason=worker_output_unavailable')
        return 1
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
