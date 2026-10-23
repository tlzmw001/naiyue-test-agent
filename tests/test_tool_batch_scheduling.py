"""Batch scheduling through the actual asynchronous create_agent assembly."""

from runtime_context_helpers import RunIdentity, typed_context
from native_application_helpers import async_test

from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.application import prepare_tool_registry

import asyncio
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import ToolException
from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent.agent_runtime import build_runtime_agent
from naiyue_test_agent.agent_runtime.resource_invocation import (
    ainvoke_agent_run_with_resources, aresume_agent_run_with_resources,
)
from naiyue_test_agent.tools.permissions import OneUseApprovalGrants
from naiyue_test_agent.agent_runtime.tool_execution.batch import (
    ToolBatchMiddleware, ToolBatchSchedulingError,
)
from naiyue_test_agent.tools import create_session_read_tool
from naiyue_test_agent.agent_runtime.tool_execution.async_batch import AsyncBatchPlan


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def make_agent(tools, responses, *, checkpointer=None):
    model = ToolCapableFake(responses=responses)
    _runtime_registry_1 = prepare_tool_registry(tools)
    _runtime_names_1 = _runtime_registry_1.names()
    return build_runtime_agent(
        model, tool_registry=_runtime_registry_1, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_1: names, system_prompt='', response_format=None,
        resolve_model=lambda _session, agent_path='root': ('fake', model),
        checkpointer=checkpointer,
    )


def tool_results(result):
    return [message for message in result['messages'] if isinstance(message, ToolMessage)]


@async_test
async def test_all_parallel_tools_can_overlap_in_one_model_reply():
    first_entered = Event()
    second_entered = Event()
    overlap = {}

    @tool
    def read_first() -> str:
        """Read a synthetic first value."""
        first_entered.set()
        overlap['first'] = second_entered.wait(5)
        return 'first'

    @tool
    def read_second() -> str:
        """Read a synthetic second value."""
        second_entered.set()
        overlap['second'] = first_entered.wait(5)
        return 'second'

    agent = make_agent([ToolSpec(read_first, read_first.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(read_second, read_second.args_schema, permissions=ToolPermission(default='allow'))], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_first', 'args': {}, 'id': 'read-1'},
            {'name': 'read_second', 'args': {}, 'id': 'read-2'},
        ]),
        AIMessage(content='done'),
    ])
    result = (await ainvoke_agent_run(
        agent, [('user', 'read both')],
        session_id='parallel', thread_id='parallel-thread', run_id='parallel-run',
    ))

    assert overlap == {'first': True, 'second': True}
    assert [(message.tool_call_id, message.content) for message in tool_results(result)] == [
        ('read-1', 'first'), ('read-2', 'second'),
    ]


@async_test
async def test_one_sequential_tool_makes_mixed_batch_execute_in_model_order():
    later_entered = Event()
    trace = []

    @tool
    def read_first() -> str:
        """Read before the synthetic write."""
        trace.append('read-1:start')
        trace.append(f'read-1:saw_later={later_entered.wait(2)}')
        trace.append('read-1:end')
        return 'read-1-result'

    @tool
    def write_probe() -> str:
        """Perform a synthetic write side effect."""
        later_entered.set()
        trace.append('write:start')
        trace.append('write:end')
        return 'write-result'


    @tool
    def read_last() -> str:
        """Read after the synthetic write."""
        later_entered.set()
        trace.append('read-3:start')
        trace.append('read-3:end')
        return 'read-3-result'

    agent = make_agent([ToolSpec(read_first, read_first.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow')), ToolSpec(read_last, read_last.args_schema, permissions=ToolPermission(default='allow'))], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_first', 'args': {}, 'id': 'mixed-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'mixed-2'},
            {'name': 'read_last', 'args': {}, 'id': 'mixed-3'},
        ]),
        AIMessage(content='done'),
    ])
    result = (await ainvoke_agent_run(
        agent, [('user', 'read write read')],
        session_id='mixed', thread_id='mixed-thread', run_id='mixed-run',
    ))

    assert trace == [
        'read-1:start', 'read-1:saw_later=False', 'read-1:end',
        'write:start', 'write:end', 'read-3:start', 'read-3:end',
    ]
    assert [message.tool_call_id for message in tool_results(result)] == [
        'mixed-1', 'mixed-2', 'mixed-3',
    ]


@async_test
async def test_ordinary_failure_allows_later_tool_body_in_sequential_batch():
    executed = []

    @tool
    def failing_probe() -> str:
        """Raise an unexpected failure before a synthetic write."""
        executed.append('first')
        raise RuntimeError('synthetic fatal failure')

    @tool
    def write_probe() -> str:
        """Record whether a later side effect was entered."""
        executed.append('write')
        return 'written'

    agent = make_agent([ToolSpec(failing_probe, failing_probe.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))], [AIMessage(
        content='', tool_calls=[
            {'name': 'failing_probe', 'args': {}, 'id': 'fatal-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'fatal-2'},
        ],
    ), AIMessage(content='handled')])

    result = (await ainvoke_agent_run(
        agent, [('user', 'run both')],
        session_id='fatal', thread_id='fatal-thread', run_id='fatal-run',
    ))
    assert executed == ['first', 'write']
    assert [(m.tool_call_id, m.status) for m in tool_results(result)] == [
        ('fatal-1', 'error'), ('fatal-2', 'success'),
    ]
    assert result['messages'][-1].content == 'handled'


@async_test
async def test_recoverable_error_result_allows_next_sequential_call():
    executed = []

    @tool
    def read_probe() -> str:
        """Return a recoverable synthetic error."""
        raise ToolException('safe probe failure')


    @tool
    def write_probe() -> str:
        """Record the later synthetic action."""
        executed.append('write')
        return 'written'

    agent = make_agent([ToolSpec(read_probe, read_probe.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_probe', 'args': {}, 'id': 'recover-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'recover-2'},
        ]),
        AIMessage(content='done'),
    ])
    result = (await ainvoke_agent_run(
        agent, [('user', 'run both')],
        session_id='recover', thread_id='recover-thread', run_id='recover-run',
    ))

    assert executed == ['write']
    assert [(message.tool_call_id, message.status) for message in tool_results(result)] == [
        ('recover-1', 'error'), ('recover-2', 'success'),
    ]


@async_test
async def test_rejected_first_tool_does_not_block_later_sequential_tool(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('private', encoding='utf-8')
    executed = []

    @tool
    def write_probe() -> str:
        """Record a synthetic action after a rejected read."""
        executed.append('write')
        return 'written'

    agent = make_agent([create_session_read_tool(), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))], [
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(outside)}, 'id': 'review-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'review-2'},
        ]),
        AIMessage(content='done'),
    ], checkpointer=InMemorySaver())

    pending = (await ainvoke_agent_run_with_resources(
        agent, [('user', 'read then write')], session_id='review',
        thread_id='review-thread', run_id='review-run', workspace_root=workspace, read_resource_roots=(),
    ))
    assert len(pending['__interrupt__']) == 1
    assert executed == []
    result = (await aresume_agent_run_with_resources(
        agent, session_id='review', thread_id='review-thread',
        run_id='review-run', workspace_root=workspace,
        decisions=['reject'], approval_grants={}, read_resource_roots=(),
    ))
    assert executed == ['write']
    assert [(message.tool_call_id, message.status) for message in tool_results(result)] == [
        ('review-1', 'error'), ('review-2', 'success'),
    ]


@async_test
async def test_approved_first_tool_completes_before_later_sequential_tool(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('approved content', encoding='utf-8')
    executed = []

    @tool
    def write_probe() -> str:
        """Record the synthetic action after an approved external read."""
        executed.append('write')
        return 'written'

    agent = make_agent([create_session_read_tool(), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))], [
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(outside)}, 'id': 'approve-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'approve-2'},
        ]),
        AIMessage(content='done'),
    ], checkpointer=InMemorySaver())
    pending = (await ainvoke_agent_run_with_resources(
        agent, [('user', 'read then write')], session_id='approve',
        thread_id='approve-thread', run_id='approve-run', workspace_root=workspace, read_resource_roots=(),
    ))
    assert len(pending['__interrupt__']) == 1
    assert executed == []
    from naiyue_test_agent.application.sessions.session_approval import pending_tool_approval, prepare_resume
    grants, _ = prepare_resume('approve', pending_tool_approval(pending), ['approve'], None)
    result = (await aresume_agent_run_with_resources(
        agent, session_id='approve', thread_id='approve-thread',
        run_id='approve-run', workspace_root=workspace,
        decisions=['approve'], read_resource_roots=(),
        approval_grants=grants,
    ))
    assert executed == ['write']
    assert [(message.tool_call_id, message.content) for message in tool_results(result)] == [
        ('approve-1', 'approved content'), ('approve-2', 'written'),
    ]


def test_invalid_execution_mode_is_rejected_at_agent_assembly():
    @tool
    def read_probe() -> str:
        """Probe invalid scheduling metadata."""
        return 'ok'

    with pytest.raises(ValueError, match='execution mode'):
        make_agent([ToolSpec(read_probe, read_probe.args_schema, execution_mode='sometimes', permissions=ToolPermission(default='allow'))], [AIMessage(content='done')])


@async_test
async def test_missing_plan_fails_closed_for_every_call_in_mixed_batch():
    @tool
    def read_probe() -> str:
        """Synthetic read that must not bypass a lost plan."""
        return 'read'

    @tool
    def write_probe() -> str:
        """Synthetic write that needs batch ordering."""
        return 'write'

    middleware = ToolBatchMiddleware(prepare_tool_registry([ToolSpec(read_probe, read_probe.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))]))
    calls = [
        {'name': 'read_probe', 'args': {}, 'id': 'lost-1'},
        {'name': 'write_probe', 'args': {}, 'id': 'lost-2'},
    ]
    executed = []
    request = SimpleNamespace(
        tool_call=calls[0],
        state={'messages': [AIMessage(content='', tool_calls=calls)],
               'tool_execution': {'tool_names': ['read_probe', 'write_probe']}},
        runtime=SimpleNamespace(context=typed_context()),
    )
    with pytest.raises(ToolBatchSchedulingError, match='plan'):
        async def downstream(_request):
            executed.append('read')
        await middleware.awrap_tool_call(request, downstream)
    assert executed == []


@async_test
async def test_duplicate_call_ids_reject_whole_batch_before_any_tool_executes():
    executed = []

    @tool
    def read_probe() -> str:
        """Record execution after duplicate model call IDs."""
        executed.append('read')
        return 'read'

    @tool
    def write_probe() -> str:
        """Record a synthetic side effect after duplicate IDs."""
        executed.append('write')
        return 'write'

    agent = make_agent([ToolSpec(read_probe, read_probe.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))], [AIMessage(
        content='', tool_calls=[
            {'name': 'read_probe', 'args': {}, 'id': 'same-id'},
            {'name': 'write_probe', 'args': {}, 'id': 'same-id'},
        ],
    )])
    with pytest.raises(Exception, match='duplicate_tool_call_ids'):
        (await ainvoke_agent_run(
            agent, [('user', 'run both')], session_id='duplicate',
            thread_id='duplicate-thread', run_id='duplicate-run',
        ))
    assert executed == []


@async_test
@pytest.mark.parametrize('concurrency', [1, 2])
async def test_sequential_batch_progresses_with_bounded_graph_concurrency(concurrency):
    trace = []

    @tool
    def read_probe() -> str:
        """Record the first batch operation."""
        trace.append('read')
        return 'read'

    @tool
    def write_probe() -> str:
        """Record the second batch operation."""
        trace.append('write')
        return 'write'

    @tool
    def final_probe() -> str:
        """Record the third batch operation."""
        trace.append('final')
        return 'final'

    agent = make_agent([ToolSpec(read_probe, read_probe.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow')), ToolSpec(final_probe, final_probe.args_schema, permissions=ToolPermission(default='allow'))], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_probe', 'args': {}, 'id': 'bounded-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'bounded-2'},
            {'name': 'final_probe', 'args': {}, 'id': 'bounded-3'},
        ]),
        AIMessage(content='done'),
    ])
    result = (await agent.ainvoke(
        {'messages': [('user', 'run all')]},
        context=typed_context(identity=RunIdentity(session_id=f'bounded-{concurrency}')),
        config={'max_concurrency': concurrency},
    ))
    assert trace == ['read', 'write', 'final']
    assert [message.tool_call_id for message in tool_results(result)] == [
        'bounded-1', 'bounded-2', 'bounded-3',
    ]


@async_test
async def test_two_sessions_share_agent_without_sharing_batch_plan():
    barrier = Barrier(2)
    trace = []

    @tool
    def read_probe(session: str) -> str:
        """Wait for another session before the synthetic write."""
        trace.append((session, 'read'))
        barrier.wait(timeout=5)
        return session

    @tool
    def write_probe(session: str) -> str:
        """Record one session's synthetic write."""
        trace.append((session, 'write'))
        return session


    class SessionModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'session-batch-probe'

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            session = next(message.content for message in messages
                           if isinstance(message, HumanMessage))
            if any(isinstance(message, ToolMessage) for message in messages):
                response = AIMessage(content='done')
            else:
                response = AIMessage(content='', tool_calls=[
                    {'name': 'read_probe', 'args': {'session': session},
                     'id': f'{session}-read'},
                    {'name': 'write_probe', 'args': {'session': session},
                     'id': f'{session}-write'},
                ])
            return ChatResult(generations=[ChatGeneration(message=response)])

    model = SessionModel()
    _runtime_registry_2 = prepare_tool_registry([ToolSpec(read_probe, read_probe.args_schema, permissions=ToolPermission(default='allow')), ToolSpec(write_probe, write_probe.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow'))])
    _runtime_names_2 = _runtime_registry_2.names()
    agent = build_runtime_agent(
        model, tool_registry=_runtime_registry_2, resolve_active_tool_names=lambda _sid, agent_path='root', names=_runtime_names_2: names, system_prompt='', response_format=None,
        resolve_model=lambda _session, agent_path='root': ('fake', model),
    )

    async def run(session):
        return (await ainvoke_agent_run(
            agent, [('user', session)], session_id=session,
            thread_id=f'{session}-thread', run_id=f'{session}-run',
        ))

    first_result, second_result = await asyncio.wait_for(asyncio.gather(run('A'), run('B')), 10)

    assert trace.index(('A', 'read')) < trace.index(('A', 'write'))
    assert trace.index(('B', 'read')) < trace.index(('B', 'write'))
    assert [message.tool_call_id for message in tool_results(first_result)] == [
        'A-read', 'A-write',
    ]
    assert [message.tool_call_id for message in tool_results(second_result)] == [
        'B-read', 'B-write',
    ]


@async_test
async def test_completed_batch_cannot_execute_same_call_twice():
    plan = AsyncBatchPlan(['once'], True, 'probe-run', ToolBatchSchedulingError)
    executed = []
    async def once():
        executed.append('once')
    async def again():
        executed.append('again')
    assert await plan.execute('once', 'probe', once, 1) is None
    with pytest.raises(ToolBatchSchedulingError, match='already completed'):
        await plan.execute('once', 'probe', again, 1)
    assert executed == ['once']


@pytest.mark.parametrize('old_value', ['not-a-number', '0', '-1', 'inf'])
def test_removed_batch_wait_configuration_does_not_affect_assembly(monkeypatch, old_value):
    monkeypatch.setenv('NAIYUE_TOOL_BATCH_WAIT_SECONDS', old_value)
    registry = prepare_tool_registry([])
    middleware = ToolBatchMiddleware(registry)
    assert middleware.registry is registry
