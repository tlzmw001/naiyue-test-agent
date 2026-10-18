"""Probe LangChain summarization with this project's dynamic model selector.

Exploratory, deterministic framework experiment; not wired into the product agent.
"""

import asyncio

from pydantic import Field

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from naiyue_test_agent.agent_runtime import RunContext, make_model_selection_middleware


class RecordingFake(FakeMessagesListChatModel):
    seen: list[list[str]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(_shape(messages))
        return super()._generate(messages, *args, **kwargs)


class FailingSummary(RecordingFake):
    attempts: int = 0

    def _generate(self, messages, *args, **kwargs):
        self.attempts += 1
        raise RuntimeError('synthetic_summary_failure')


def _make_agent(summary_model):
    selected = {'probe': 'A'}
    model_a = RecordingFake(
        responses=[
            AIMessage(content='', tool_calls=[
                {'name': 'read_probe', 'args': {}, 'id': 'probe-1'},
            ], response_metadata={'finish_reason': 'tool_calls'}),
            AIMessage(content='first-run-done', response_metadata={'finish_reason': 'stop'}),
        ],
        profile={'max_input_tokens': 100},
    )
    model_b = RecordingFake(
        responses=[AIMessage(content='second-run-done',
                             response_metadata={'finish_reason': 'stop'})],
        profile={'max_input_tokens': 200},
    )
    models = {'A': model_a, 'B': model_b}

    @tool
    def read_probe() -> str:
        """Return a synthetic value for a context-compaction experiment."""
        return 'synthetic-tool-result'

    summary = SummarizationMiddleware(
        model=summary_model, trigger=('fraction', 0.5), keep=('messages', 3),
        token_counter=lambda messages: 15 * len(messages),
    )
    agent = create_agent(
        model=model_a, tools=[read_probe], context_schema=RunContext,
        middleware=[
            make_model_selection_middleware(
                lambda session_id, agent_path='root': (selected[session_id], models[selected[session_id]]),
            ),
            summary,
        ],
        checkpointer=InMemorySaver(),
    )
    return agent, selected, model_a, model_b


async def _invoke(agent, text):
    return (await agent.ainvoke(
        {'messages': [('user', text)]}, context={'session_id': 'probe'},
        config={'configurable': {'thread_id': 'probe-thread'}},
    ))


def _shape(messages):
    result = []
    for message in messages:
        if isinstance(message, ToolMessage):
            result.append(f'tool:{message.tool_call_id}')
        elif isinstance(message, AIMessage) and message.tool_calls:
            result.append('ai_call:' + ','.join(call['id'] for call in message.tool_calls))
        elif isinstance(message, HumanMessage) and message.additional_kwargs.get('lc_source') == 'summarization':
            result.append('summary')
        else:
            result.append(message.type)
    return result


async def main():
    summary_model = RecordingFake(
        responses=[AIMessage(content='synthetic-summary')],
        profile={'max_input_tokens': 100},
    )
    agent, selected, model_a, model_b = _make_agent(summary_model)
    first = (await _invoke(agent, 'first-user-message'))
    assert _shape(first['messages']) == ['human', 'ai_call:probe-1', 'tool:probe-1', 'ai']
    first_checkpoint_id = agent.get_state(
        {'configurable': {'thread_id': 'probe-thread'}}
    ).config['configurable']['checkpoint_id']

    selected['probe'] = 'B'
    second = (await _invoke(agent, 'second-user-message'))
    current_shape = _shape(second['messages'])
    assert current_shape == [
        'summary', 'ai_call:probe-1', 'tool:probe-1', 'ai', 'human', 'ai',
    ]
    assert len(summary_model.seen) == 1
    assert len(model_a.seen) == 2 and len(model_b.seen) == 1
    assert model_b.seen[0] == current_shape[:-1]
    previous = agent.get_state({'configurable': {
        'thread_id': 'probe-thread', 'checkpoint_id': first_checkpoint_id,
    }})
    assert _shape(previous.values['messages']) == _shape(first['messages'])
    assert _shape(agent.get_state(
        {'configurable': {'thread_id': 'probe-thread'}}
    ).values['messages']) == current_shape
    print('success selected=B summary_calls=1 bound_profile=100 active_profile=200')
    print('after_compaction=' + ','.join(current_shape))
    print('previous_checkpoint=' + ','.join(_shape(previous.values['messages'])))

    failing_summary = FailingSummary(
        responses=[AIMessage(content='unused')], profile={'max_input_tokens': 100},
    )
    failed_agent, failed_selection, _a, _b = _make_agent(failing_summary)
    (await _invoke(failed_agent, 'first-user-message'))
    failed_selection['probe'] = 'B'
    try:
        (await _invoke(failed_agent, 'second-user-message'))
    except RuntimeError as error:
        assert str(error) == 'synthetic_summary_failure'
    else:
        raise AssertionError('summary failure must propagate')
    failed_shape = _shape(failed_agent.get_state(
        {'configurable': {'thread_id': 'probe-thread'}}
    ).values['messages'])
    assert failed_shape == ['human', 'ai_call:probe-1', 'tool:probe-1', 'ai', 'human']
    assert len(_b.seen) == 0
    print(f'failure summary_attempts={failing_summary.attempts} '
          f'active_model_calls=0 checkpoint={",".join(failed_shape)}')


if __name__ == '__main__':
    asyncio.run(main())
