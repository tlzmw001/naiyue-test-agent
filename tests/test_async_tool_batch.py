"""Async dispatch preserves tool order, finite concurrency and error results."""

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

import asyncio

import pytest
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.agent_runtime import build_runtime_agent
from test_async_application import StreamingToolModel


def graph(model, tools):
    _runtime_registry_1 = prepare_tool_registry(tools)
    _runtime_names_1 = _runtime_registry_1.names()
    return build_runtime_agent(model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=lambda _: ('A', model), record_selection=lambda *_: None)


def batch(*labels, name='probe', reason='tool_calls'):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': {'label': label}, 'id': str(i)}
        for i, label in enumerate(labels)], response_metadata={'finish_reason': reason})


async def wait_entered_or_failure(task, event):
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait((task, waiter), timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert done
        if task in done:
            await task
        await asyncio.wait_for(waiter, 5)
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


def test_parallel_batch_respects_config_and_preserves_result_order():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        active, peak, seen = 0, 0, []

        @tool
        async def probe(label: str) -> str:
            """Record one synthetic concurrent call."""
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            seen.append(label)
            if len(seen) == 2:
                entered.set()
            await release.wait()
            active -= 1
            return label

        model = StreamingToolModel(replies=[batch('A', 'B', 'C'), AIMessage(content='done')])
        task = asyncio.create_task(graph(model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))]).ainvoke(
            {'messages': [('user', 'parallel')]}, context={'session_id': 's'}, config={'max_concurrency': 2}))
        try:
            await wait_entered_or_failure(task, entered)
            assert len(seen) == 2 and peak == 2
            release.set()
            result = await task
            assert peak == 2 and seen == ['A', 'B', 'C']
            assert [m.tool_call_id for m in result['messages'] if isinstance(m, ToolMessage)] == ['0', '1', '2']
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('soft_error', [False, True])
def test_sequential_batch_preserves_full_chain_order(soft_error):
    async def scenario():
        seen = []

        @tool
        async def probe(label: str) -> str:
            """Record one synthetic ordered call."""
            seen.append(label)
            if soft_error and label == 'B':
                raise ToolException('safe blocked')
            return label

        model = StreamingToolModel(replies=[batch('A', 'B', 'C'), AIMessage(content='done')])
        result = await graph(model, [ToolSpec(probe, probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))]).ainvoke({'messages': [('user', 'ordered')]},
                                                   context={'session_id': 's'})
        assert seen == ['A', 'B', 'C']
        results = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        assert [m.tool_call_id for m in results] == ['0', '1', '2']
        assert results[1].status == ('error' if soft_error else 'success')
    asyncio.run(scenario())


def test_ordinary_batch_failure_keeps_later_tools_runnable():
    async def scenario():
        seen, error = [], RuntimeError('PRIVATE')

        @tool
        async def probe(label: str) -> str:
            """Raise a synthetic fatal error."""
            seen.append(label)
            raise error

        model = StreamingToolModel(replies=[batch('A', 'B', 'C'), AIMessage(content='handled')])
        result = await graph(model, [ToolSpec(probe, probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))]).ainvoke({'messages': [('user', 'fatal')]}, context={'session_id': 's'})
        assert seen == ['A', 'B', 'C']
        assert [(m.tool_call_id, m.status) for m in result['messages'] if isinstance(m, ToolMessage)] == [
            ('0', 'error'), ('1', 'error'), ('2', 'error'),
        ]
        assert result['messages'][-1].content == 'handled'
    asyncio.run(scenario())


def test_truncated_calls_are_error_results_without_execution():
    async def scenario():
        seen = []

        @tool
        async def probe(label: str) -> str:
            """Record a call that must be guarded."""
            seen.append(label)
            return label

        model = StreamingToolModel(replies=[batch('A', reason='length'), AIMessage(content='repaired')])
        result = await graph(model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))]).ainvoke({'messages': [('user', 'guard')]}, context={'session_id': 's'})
        assert seen == []
        message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        assert message.status == 'error' and 'truncated' in message.content
    asyncio.run(scenario())
