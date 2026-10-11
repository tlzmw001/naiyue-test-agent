"""Batch scheduling through the actual synchronous create_agent assembly."""

from concurrent.futures import ThreadPoolExecutor
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

from naiyue_test_agent.agent_runtime import (
    build_runtime_agent, invoke_agent_run,
)
from naiyue_test_agent.agent_runtime.resource_invocation import (
    invoke_agent_run_with_resources, resume_agent_run_with_resources,
)
from naiyue_test_agent.agent_runtime.tool_approval import OneUseApprovalGrants
from naiyue_test_agent.agent_runtime.tool_batch import (
    ToolBatchMiddleware, ToolBatchSchedulingError, _SequentialPlan,
)
from naiyue_test_agent.agent_runtime.tools import create_session_read_tool


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def make_agent(tools, responses, *, checkpointer=None):
    model = ToolCapableFake(responses=responses)
    return build_runtime_agent(
        model, tools=tools, system_prompt='', response_format=None,
        resolve_model=lambda _session: ('fake', model),
        record_selection=lambda _session, _model: None,
        checkpointer=checkpointer,
    )


def tool_results(result):
    return [message for message in result['messages'] if isinstance(message, ToolMessage)]


def test_all_parallel_tools_can_overlap_in_one_model_reply():
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

    agent = make_agent([read_first, read_second], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_first', 'args': {}, 'id': 'read-1'},
            {'name': 'read_second', 'args': {}, 'id': 'read-2'},
        ]),
        AIMessage(content='done'),
    ])
    result = invoke_agent_run(
        agent, [('user', 'read both')],
        session_id='parallel', thread_id='parallel-thread', run_id='parallel-run',
    )

    assert overlap == {'first': True, 'second': True}
    assert [(message.tool_call_id, message.content) for message in tool_results(result)] == [
        ('read-1', 'first'), ('read-2', 'second'),
    ]


def test_one_sequential_tool_makes_mixed_batch_execute_in_model_order():
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

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}

    @tool
    def read_last() -> str:
        """Read after the synthetic write."""
        later_entered.set()
        trace.append('read-3:start')
        trace.append('read-3:end')
        return 'read-3-result'

    agent = make_agent([read_first, write_probe, read_last], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_first', 'args': {}, 'id': 'mixed-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'mixed-2'},
            {'name': 'read_last', 'args': {}, 'id': 'mixed-3'},
        ]),
        AIMessage(content='done'),
    ])
    result = invoke_agent_run(
        agent, [('user', 'read write read')],
        session_id='mixed', thread_id='mixed-thread', run_id='mixed-run',
    )

    assert trace == [
        'read-1:start', 'read-1:saw_later=False', 'read-1:end',
        'write:start', 'write:end', 'read-3:start', 'read-3:end',
    ]
    assert [message.tool_call_id for message in tool_results(result)] == [
        'mixed-1', 'mixed-2', 'mixed-3',
    ]


def test_fatal_failure_skips_later_tool_body_in_sequential_batch():
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

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}
    agent = make_agent([failing_probe, write_probe], [AIMessage(
        content='', tool_calls=[
            {'name': 'failing_probe', 'args': {}, 'id': 'fatal-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'fatal-2'},
        ],
    )])

    with pytest.raises(RuntimeError, match='synthetic fatal failure'):
        invoke_agent_run(
            agent, [('user', 'run both')],
            session_id='fatal', thread_id='fatal-thread', run_id='fatal-run',
        )
    assert executed == ['first']


def test_recoverable_error_result_allows_next_sequential_call():
    executed = []

    @tool
    def read_probe() -> str:
        """Return a recoverable synthetic error."""
        raise ToolException('safe probe failure')

    read_probe.handle_tool_error = True

    @tool
    def write_probe() -> str:
        """Record the later synthetic action."""
        executed.append('write')
        return 'written'

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}
    agent = make_agent([read_probe, write_probe], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_probe', 'args': {}, 'id': 'recover-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'recover-2'},
        ]),
        AIMessage(content='done'),
    ])
    result = invoke_agent_run(
        agent, [('user', 'run both')],
        session_id='recover', thread_id='recover-thread', run_id='recover-run',
    )

    assert executed == ['write']
    assert [(message.tool_call_id, message.status) for message in tool_results(result)] == [
        ('recover-1', 'error'), ('recover-2', 'success'),
    ]


def test_rejected_first_tool_does_not_block_later_sequential_tool(tmp_path):
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

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}
    agent = make_agent([create_session_read_tool(), write_probe], [
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(outside)}, 'id': 'review-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'review-2'},
        ]),
        AIMessage(content='done'),
    ], checkpointer=InMemorySaver())

    pending = invoke_agent_run_with_resources(
        agent, [('user', 'read then write')], session_id='review',
        thread_id='review-thread', run_id='review-run', workspace_root=workspace, read_resource_roots=(),
    )
    assert len(pending['__interrupt__']) == 1
    assert executed == []
    result = resume_agent_run_with_resources(
        agent, session_id='review', thread_id='review-thread',
        run_id='review-run', workspace_root=workspace,
        decisions=['reject'], approval_grants={}, read_resource_roots=(),
    )
    assert executed == ['write']
    assert [(message.tool_call_id, message.status) for message in tool_results(result)] == [
        ('review-1', 'error'), ('review-2', 'success'),
    ]


def test_approved_first_tool_completes_before_later_sequential_tool(tmp_path):
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

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}
    agent = make_agent([create_session_read_tool(), write_probe], [
        AIMessage(content='', tool_calls=[
            {'name': 'read', 'args': {'path': str(outside)}, 'id': 'approve-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'approve-2'},
        ]),
        AIMessage(content='done'),
    ], checkpointer=InMemorySaver())
    pending = invoke_agent_run_with_resources(
        agent, [('user', 'read then write')], session_id='approve',
        thread_id='approve-thread', run_id='approve-run', workspace_root=workspace, read_resource_roots=(),
    )
    assert len(pending['__interrupt__']) == 1
    assert executed == []
    result = resume_agent_run_with_resources(
        agent, session_id='approve', thread_id='approve-thread',
        run_id='approve-run', workspace_root=workspace,
        decisions=['approve'], read_resource_roots=(),
        approval_grants={'read': OneUseApprovalGrants({'approve-1': outside.resolve()})},
    )
    assert executed == ['write']
    assert [(message.tool_call_id, message.content) for message in tool_results(result)] == [
        ('approve-1', 'approved content'), ('approve-2', 'written'),
    ]


def test_invalid_execution_mode_is_rejected_at_agent_assembly():
    @tool
    def read_probe() -> str:
        """Probe invalid scheduling metadata."""
        return 'ok'

    read_probe.metadata = {'naiyue_execution_mode': 'sometimes'}
    with pytest.raises(ValueError, match='execution mode'):
        make_agent([read_probe], [AIMessage(content='done')])


def test_missing_plan_fails_closed_for_every_call_in_mixed_batch():
    @tool
    def read_probe() -> str:
        """Synthetic read that must not bypass a lost plan."""
        return 'read'

    @tool
    def write_probe() -> str:
        """Synthetic write that needs batch ordering."""
        return 'write'

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}
    middleware = ToolBatchMiddleware([read_probe, write_probe])
    calls = [
        {'name': 'read_probe', 'args': {}, 'id': 'lost-1'},
        {'name': 'write_probe', 'args': {}, 'id': 'lost-2'},
    ]
    executed = []
    request = SimpleNamespace(
        tool_call=calls[0],
        state={'messages': [AIMessage(content='', tool_calls=calls)]},
        runtime=SimpleNamespace(context={}),
    )
    with pytest.raises(ToolBatchSchedulingError, match='plan'):
        middleware.wrap_tool_call(request, lambda _request: executed.append('read'))
    assert executed == []


def test_duplicate_call_ids_reject_whole_batch_before_any_tool_executes():
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

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}
    agent = make_agent([read_probe, write_probe], [AIMessage(
        content='', tool_calls=[
            {'name': 'read_probe', 'args': {}, 'id': 'same-id'},
            {'name': 'write_probe', 'args': {}, 'id': 'same-id'},
        ],
    )])
    with pytest.raises(Exception, match='duplicate_tool_call_ids'):
        invoke_agent_run(
            agent, [('user', 'run both')], session_id='duplicate',
            thread_id='duplicate-thread', run_id='duplicate-run',
        )
    assert executed == []


@pytest.mark.parametrize('concurrency', [1, 2])
def test_sequential_batch_progresses_with_bounded_graph_concurrency(concurrency):
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

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}
    agent = make_agent([read_probe, write_probe, final_probe], [
        AIMessage(content='', tool_calls=[
            {'name': 'read_probe', 'args': {}, 'id': 'bounded-1'},
            {'name': 'write_probe', 'args': {}, 'id': 'bounded-2'},
            {'name': 'final_probe', 'args': {}, 'id': 'bounded-3'},
        ]),
        AIMessage(content='done'),
    ])
    result = agent.invoke(
        {'messages': [('user', 'run all')]},
        context={'session_id': f'bounded-{concurrency}'},
        config={'max_concurrency': concurrency},
    )
    assert trace == ['read', 'write', 'final']
    assert [message.tool_call_id for message in tool_results(result)] == [
        'bounded-1', 'bounded-2', 'bounded-3',
    ]


def test_two_sessions_share_agent_without_sharing_batch_plan():
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

    write_probe.metadata = {'naiyue_execution_mode': 'sequential'}

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
    agent = build_runtime_agent(
        model, tools=[read_probe, write_probe], system_prompt='', response_format=None,
        resolve_model=lambda _session: ('fake', model),
        record_selection=lambda _session, _model: None,
    )

    def run(session):
        return invoke_agent_run(
            agent, [('user', session)], session_id=session,
            thread_id=f'{session}-thread', run_id=f'{session}-run',
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run, 'A')
        second = executor.submit(run, 'B')
        first_result = first.result(timeout=10)
        second_result = second.result(timeout=10)

    assert trace.index(('A', 'read')) < trace.index(('A', 'write'))
    assert trace.index(('B', 'read')) < trace.index(('B', 'write'))
    assert [message.tool_call_id for message in tool_results(first_result)] == [
        'A-read', 'A-write',
    ]
    assert [message.tool_call_id for message in tool_results(second_result)] == [
        'B-read', 'B-write',
    ]


def test_completed_batch_cannot_execute_same_call_twice():
    plan = _SequentialPlan(['once'], wait_seconds=1, run_id='probe-run')
    executed = []
    assert plan.execute('once', 'probe', lambda: executed.append('once')) is None
    with pytest.raises(ToolBatchSchedulingError, match='already completed'):
        plan.execute('once', 'probe', lambda: executed.append('again'))
    assert executed == ['once']


def test_invalid_batch_wait_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv('NAIYUE_TOOL_BATCH_WAIT_SECONDS', 'not-a-number')
    with pytest.raises(ValueError, match='wait duration'):
        ToolBatchMiddleware([])
