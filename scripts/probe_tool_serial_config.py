"""Probe whole-run max_concurrency=1, HITL resume, and fatal-error behavior."""

from threading import Event

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def main() -> None:
    first_entered = Event()
    second_entered = Event()
    trace = []

    @tool
    def first_probe() -> str:
        """Observe the first tool in a synthetic batch."""
        trace.append('first:start')
        first_entered.set()
        trace.append(f'first:saw_second={second_entered.wait(2)}')
        trace.append('first:end')
        return 'first-result'

    @tool
    def second_probe() -> str:
        """Observe the second tool in a synthetic batch."""
        trace.append('second:start')
        second_entered.set()
        trace.append('second:end')
        return 'second-result'

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'first_probe', 'args': {}, 'id': 'call-1'},
            {'name': 'second_probe', 'args': {}, 'id': 'call-2'},
        ]),
        AIMessage(content='done'),
    ])
    agent = create_agent(model, tools=[first_probe, second_probe])
    result = agent.invoke(
        {'messages': [('user', 'probe')]}, config={'max_concurrency': 1},
    )
    assert trace == [
        'first:start', 'first:saw_second=False', 'first:end',
        'second:start', 'second:end',
    ]
    assert [(message.tool_call_id, message.content) for message in result['messages']
            if isinstance(message, ToolMessage)] == [
        ('call-1', 'first-result'), ('call-2', 'second-result'),
    ]
    print(f'serial_trace={trace}')

    trace.clear()
    first_entered.clear()
    second_entered.clear()
    reviewed_model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'first_probe', 'args': {}, 'id': 'review-1'},
            {'name': 'second_probe', 'args': {}, 'id': 'review-2'},
        ]),
        AIMessage(content='done'),
    ])
    reviewed_agent = create_agent(
        reviewed_model, tools=[first_probe, second_probe],
        middleware=[HumanInTheLoopMiddleware(interrupt_on={
            'second_probe': {'allowed_decisions': ['approve', 'reject']},
        })],
        checkpointer=InMemorySaver(),
    )
    config = {'max_concurrency': 1, 'configurable': {'thread_id': 'batch-review-probe'}}
    pending = reviewed_agent.invoke({'messages': [('user', 'probe')]}, config=config)
    assert len(pending.get('__interrupt__', [])) == 1
    assert trace == []
    resumed = reviewed_agent.invoke(
        Command(resume={'decisions': [{'type': 'approve'}]}), config=config,
    )
    assert trace == [
        'first:start', 'first:saw_second=False', 'first:end',
        'second:start', 'second:end',
    ]
    assert [(message.tool_call_id, message.content) for message in resumed['messages']
            if isinstance(message, ToolMessage)] == [
        ('review-1', 'first-result'), ('review-2', 'second-result'),
    ]
    print(f'hitl_resume_trace={trace}')

    failed_trace = []

    @tool
    def failing_probe() -> str:
        """Raise a synthetic unexpected tool failure."""
        failed_trace.append('first:failed')
        raise RuntimeError('synthetic failure')

    @tool
    def later_probe() -> str:
        """Observe whether a later tool still starts."""
        failed_trace.append('second:started')
        return 'later-result'

    failed_model = ToolCapableFake(responses=[AIMessage(content='', tool_calls=[
        {'name': 'failing_probe', 'args': {}, 'id': 'failed-1'},
        {'name': 'later_probe', 'args': {}, 'id': 'failed-2'},
    ])])
    failed_agent = create_agent(failed_model, tools=[failing_probe, later_probe])
    try:
        failed_agent.invoke({'messages': [('user', 'probe')]},
                            config={'max_concurrency': 1})
    except RuntimeError:
        pass
    else:
        raise AssertionError('Expected the first tool failure to propagate')
    assert failed_trace == ['first:failed', 'second:started']
    print(f'failure_trace={failed_trace}')


if __name__ == '__main__':
    main()
