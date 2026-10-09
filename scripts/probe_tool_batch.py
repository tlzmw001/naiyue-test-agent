"""Verify current create_agent tool fan-out with a deterministic fake model."""

from threading import Event

from langchain.agents import create_agent
from langchain.agents.middleware import after_model
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.agent_runtime import build_runtime_agent, invoke_agent_run


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def main() -> None:
    first_entered = Event()
    second_entered = Event()
    observations = {}
    executions = []

    @tool
    def first_probe() -> str:
        """First tool for observing batch execution."""
        first_entered.set()
        observations['first_saw_second'] = second_entered.wait(5)
        executions.append('first')
        return 'first-result'

    @tool
    def second_probe() -> str:
        """Second tool for observing batch execution."""
        second_entered.set()
        observations['second_saw_first'] = first_entered.wait(5)
        executions.append('second')
        return 'second-result'

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'first_probe', 'args': {}, 'id': 'call-1'},
            {'name': 'second_probe', 'args': {}, 'id': 'call-2'},
        ], response_metadata={'finish_reason': 'tool_calls'}),
        AIMessage(content='done', response_metadata={'finish_reason': 'stop'}),
    ])
    agent = build_runtime_agent(
        model, tools=[first_probe, second_probe], system_prompt='', response_format=None,
        resolve_model=lambda _session: ('fake', model),
        record_selection=lambda _session, _model: None,
    )
    result = invoke_agent_run(
        agent, [('user', 'probe')], session_id='probe-session',
        thread_id='probe-thread', run_id='probe-run',
    )
    tool_messages = [
        (message.tool_call_id, message.content)
        for message in result['messages'] if isinstance(message, ToolMessage)
    ]
    assert observations == {'first_saw_second': True, 'second_saw_first': True}
    assert tool_messages == [
        ('call-1', 'first-result'), ('call-2', 'second-result'),
    ]
    assert sorted(executions) == ['first', 'second']
    print(f'observations={observations}')
    print(f'tool_messages={tool_messages}')

    # A separate hook feasibility check; this is not wired into the project agent.
    @after_model
    def reject_multi_call(state, _runtime):
        message = state['messages'][-1]
        if not isinstance(message, AIMessage) or len(message.tool_calls) < 2:
            return None
        return {'messages': [ToolMessage(
            content='Call one tool at a time.', tool_call_id=call['id'],
            name=call['name'], status='error',
        ) for call in message.tool_calls]}

    executions.clear()
    gate_model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'first_probe', 'args': {}, 'id': 'gate-1'},
            {'name': 'second_probe', 'args': {}, 'id': 'gate-2'},
        ]),
        AIMessage(content='will retry'),
    ])
    gated_agent = create_agent(
        gate_model, tools=[first_probe, second_probe], middleware=[reject_multi_call],
    )
    gated_result = gated_agent.invoke({'messages': [('user', 'probe')]})
    gate_messages = [
        (message.tool_call_id, message.status)
        for message in gated_result['messages'] if isinstance(message, ToolMessage)
    ]
    assert executions == []
    assert gate_messages == [('gate-1', 'error'), ('gate-2', 'error')]
    assert gated_result['messages'][-1].content == 'will retry'
    print(f'gate_executions={executions}')
    print(f'gate_tool_messages={gate_messages}')


if __name__ == '__main__':
    main()
