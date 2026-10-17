"""Make middleware registration and a complete tool-loop order observable."""

from native_application_helpers import async_test


from langchain.agents import create_agent
import pytest

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage


class ToolCapableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@async_test
async def test_hooks_are_registered_and_ordered_across_two_model_calls():
    trace = []

    class TraceMiddleware(AgentMiddleware):
        def __init__(self, label):
            self.label = label

        @property
        def name(self):
            return self.label

        def _record(self, hook, state):
            trace.append((f'{self.label}.{hook}', [m.type for m in state['messages']]))

        def before_agent(self, state, runtime):
            self._record('before_agent', state)

        def before_model(self, state, runtime):
            self._record('before_model', state)

        def after_model(self, state, runtime):
            self._record('after_model', state)

        def after_agent(self, state, runtime):
            self._record('after_agent', state)

    class RecordingModel(ToolCapableFake):
        def _generate(self, messages, *args, **kwargs):
            trace.append(('model', [m.type for m in messages]))
            return super()._generate(messages, *args, **kwargs)

    @tool
    def echo_probe(value: str) -> str:
        """Echo one synthetic value for the middleware-order test."""
        trace.append(('tool', None))
        return f'echo: {value}'

    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'echo_probe', 'args': {'value': 'ping'}, 'id': 'call-1'},
        ]),
        AIMessage(content='done'),
    ])
    agent = create_agent(
        model=model, tools=[echo_probe],
        middleware=[TraceMiddleware('A'), AgentMiddleware(), TraceMiddleware('B')],
    )

    assert trace == []
    for label in ('A', 'B'):
        for hook in ('before_agent', 'before_model', 'after_model', 'after_agent'):
            assert f'{label}.{hook}' in agent.nodes
            assert f'AgentMiddleware.{hook}' not in agent.nodes

    result = (await agent.ainvoke({'messages': [('user', 'Echo ping, then answer.')]}))

    assert [event for event, _ in trace] == [
        'A.before_agent', 'B.before_agent',
        'A.before_model', 'B.before_model', 'model',
        'B.after_model', 'A.after_model',
        'tool',
        'A.before_model', 'B.before_model', 'model',
        'B.after_model', 'A.after_model',
        'B.after_agent', 'A.after_agent',
    ]
    assert [types for event, types in trace if event == 'A.before_model'] == [
        ['human'], ['human', 'ai', 'tool'],
    ]
    assert [types for event, types in trace if event == 'A.after_model'] == [
        ['human', 'ai'], ['human', 'ai', 'tool', 'ai'],
    ]
    assert trace[-1][1] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][2].content == 'echo: ping'
    assert result['messages'][2].tool_call_id == result['messages'][1].tool_calls[0]['id']
    assert result['messages'][-1].content == 'done'


@async_test
@pytest.mark.parametrize(('scope', 'outcome'), [
    ('model', 'pass'), ('model', 'short'), ('tool', 'short'),
    ('model', 'error'), ('tool', 'error'),
], ids=['pass', 'model-short', 'tool-short', 'model-error', 'tool-error'])
async def test_wrap_handlers_enter_return_and_preserve_boundaries(scope, outcome):
    trace = []
    failure = RuntimeError('synthetic failure')

    class WrapMiddleware(AgentMiddleware):
        def __init__(self, label):
            self.label = label

        @property
        def name(self):
            return self.label

        async def _call(self, kind, request, handler):
            trace.append(f'{self.label}.{kind}.enter')
            try:
                if self.label == 'B' and scope == kind and outcome == 'short':
                    trace.append(f'B.{kind}.short')
                    if kind == 'model':
                        result = ModelResponse(result=[AIMessage(content='short answer')])
                    else:
                        result = ToolMessage(
                            content='synthetic tool result', name=request.tool_call['name'],
                            tool_call_id=request.tool_call['id'],
                        )
                else:
                    result = await handler(request)
            except RuntimeError:
                trace.append(f'{self.label}.{kind}.error')
                raise
            trace.append(f'{self.label}.{kind}.exit')
            return result

        async def awrap_model_call(self, request, handler):
            return await self._call('model', request, handler)

        async def awrap_tool_call(self, request, handler):
            return await self._call('tool', request, handler)

    class RecordingModel(ToolCapableFake):
        def _generate(self, messages, *args, **kwargs):
            trace.append('model')
            if scope == 'model' and outcome == 'error':
                raise failure
            return super()._generate(messages, *args, **kwargs)

    @tool
    def echo_probe(value: str) -> str:
        """Echo a value or inject a controlled tool failure."""
        trace.append('tool')
        if scope == 'tool' and outcome == 'error':
            raise failure
        return f'echo: {value}'

    model = RecordingModel(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'echo_probe', 'args': {'value': 'ping'}, 'id': 'call-1'},
        ]),
        AIMessage(content='done'),
    ])
    agent = create_agent(
        model=model, tools=[echo_probe],
        middleware=[WrapMiddleware('A'), WrapMiddleware('B')],
    )
    assert trace == []
    assert 'A.wrap_model_call' not in agent.nodes
    assert 'B.wrap_tool_call' not in agent.nodes

    if outcome == 'error':
        with pytest.raises(RuntimeError) as caught:
            (await agent.ainvoke({'messages': [('user', 'Echo ping, then answer.')]}))
        assert caught.value is failure
    else:
        result = (await agent.ainvoke({'messages': [('user', 'Echo ping, then answer.')]}))
        assert result['messages'][-1].content == ('short answer' if scope == 'model' and outcome == 'short' else 'done')
        if scope == 'tool' and outcome == 'short':
            assert result['messages'][2].content == 'synthetic tool result'
        if outcome == 'pass' or scope == 'tool' and outcome == 'short':
            assert result['messages'][2].tool_call_id == result['messages'][1].tool_calls[0]['id']

    model_pass = ['A.model.enter', 'B.model.enter', 'model', 'B.model.exit', 'A.model.exit']
    tool_pass = ['A.tool.enter', 'B.tool.enter', 'tool', 'B.tool.exit', 'A.tool.exit']
    if outcome == 'pass':
        expected = model_pass + tool_pass + model_pass
    elif scope == 'model' and outcome == 'short':
        expected = ['A.model.enter', 'B.model.enter', 'B.model.short', 'B.model.exit', 'A.model.exit']
    elif outcome == 'short':
        expected = model_pass + ['A.tool.enter', 'B.tool.enter', 'B.tool.short', 'B.tool.exit', 'A.tool.exit'] + model_pass
    elif scope == 'model':
        expected = ['A.model.enter', 'B.model.enter', 'model', 'B.model.error', 'A.model.error']
    else:
        expected = model_pass + ['A.tool.enter', 'B.tool.enter', 'tool', 'B.tool.error', 'A.tool.error']
    assert trace == expected


@async_test
@pytest.mark.parametrize('mode', ['ainvoke', 'astream'])
async def test_call_modes_share_loop_but_differ_in_output(mode):
    calls = []
    parts = []

    @tool
    def echo_probe(value: str) -> str:
        """Echo one synthetic value for the call-mode comparison."""
        calls.append(value)
        return f'echo: {value}'

    model = ToolCapableFake(responses=[
        AIMessage(content='', tool_calls=[
            {'name': 'echo_probe', 'args': {'value': 'ping'}, 'id': 'call-1'},
        ]),
        AIMessage(content='done'),
    ])
    agent = create_agent(model=model, tools=[echo_probe])
    inputs = {'messages': [('user', 'Echo ping, then answer.')]}

    async def async_call():
        if mode == 'ainvoke':
            return await agent.ainvoke(inputs)
        async for part in agent.astream(
            inputs, stream_mode=['updates', 'values'], version='v2',
        ):
            parts.append(part)
        return [part['data'] for part in parts if part['type'] == 'values'][-1]

    result = await async_call()

    assert calls == ['ping']
    assert [message.type for message in result['messages']] == ['human', 'ai', 'tool', 'ai']
    assert result['messages'][2].content == 'echo: ping'
    assert result['messages'][2].tool_call_id == result['messages'][1].tool_calls[0]['id']
    assert result['messages'][-1].content == 'done'
    if mode == 'astream':
        assert all(part['ns'] == () for part in parts)
        updates = [part['data'] for part in parts if part['type'] == 'updates']
        assert [list(update) for update in updates] == [['model'], ['tools'], ['model']]
        snapshots = [part['data']['messages'] for part in parts if part['type'] == 'values']
        assert [[message.type for message in snapshot] for snapshot in snapshots] == [
            ['human'], ['human', 'ai'], ['human', 'ai', 'tool'],
            ['human', 'ai', 'tool', 'ai'],
        ]
        assert [message.type for message in updates[0]['model']['messages']] == ['ai']
    else:
        assert parts == []
