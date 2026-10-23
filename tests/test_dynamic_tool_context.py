"""Request selection stays aligned with compaction, retries and concurrent sessions."""

from native_application_helpers import async_test

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_context_chain import overflow_error
from test_dynamic_tools import DynamicModel, call, definition


@async_test
async def test_each_request_prepares_one_view_before_compaction_and_model(monkeypatch):
    from naiyue_test_agent.agent_runtime import request_preparation
    from naiyue_test_agent.agent_runtime.tool_execution.toolset import ToolSetMiddleware
    trace, budgets = [], []
    resolve = application.SessionService.resolve_request_configuration
    def selected(self, *args):
        trace.append('selection')
        return resolve(self, *args)
    monkeypatch.setattr(application.SessionService, 'resolve_request_configuration', selected)
    def budget(**kwargs):
        trace.append('compaction')
        budgets.append(kwargs)
        return None
    async def abudget(messages, model, **kwargs):
        return budget(messages=messages, model=model, **kwargs)
    monkeypatch.setattr(request_preparation, '_acompact_messages', abudget)
    prepare = ToolSetMiddleware.prepare_tool_view
    def prepare_view(self, runtime, **kwargs):
        trace.append('tool_view')
        return prepare(self, runtime, **kwargs)
    monkeypatch.setattr(ToolSetMiddleware, 'prepare_tool_view', prepare_view)
    spec = definition(prompt_snippet='PROMPT_SNIPPET')
    model = DynamicModel(responses=[call(), AIMessage(content='done')])
    model.on_request = lambda _: trace.append('model')
    async def scenario():
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_async_local_application('synthetic', tools=[spec], system_prompt='BASE', response_format=None)
        sid = await app.sessions.create('A')
        try:
            await app.runs.start(sid, [('user', 'run')])
        finally:
            await app.aclose()
    await scenario()
    assert trace == ['selection', 'tool_view', 'compaction', 'model'] * 2
    assert len(budgets) == 2
    for index, request in enumerate(budgets):
        assert request['tools'] == [spec.tool] and request['tools'][0] is spec.tool
        assert request['system_messages'][0].content == model.seen[index][0].content


@async_test
async def test_overflow_retry_keeps_view_then_next_request_refreshes(monkeypatch):
    from naiyue_test_agent.agent_runtime import context_recovery as recovery
    inputs = []
    def compact(**kwargs):
        inputs.append(kwargs)
        return {'messages': [RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content='retained request')]}
    async def acompact(**kwargs):
        return compact(**kwargs)
    monkeypatch.setattr(recovery, '_acompact_messages', compact)
    monkeypatch.setattr(recovery, '_acompact_messages', acompact)
    class OverflowModel(DynamicModel):
        def _generate(self, *args, **kwargs):
            result = super()._generate(*args, **kwargs)
            if len(self.seen) == 1:
                raise overflow_error()
            return result
        async def _agenerate(self, *args, **kwargs):
            result = await super()._agenerate(*args, **kwargs)
            if len(self.seen) == 1:
                raise overflow_error()
            return result
    async def scenario():
        calls = []
        model = OverflowModel(responses=[AIMessage(content='rejected'), call('old'), AIMessage(content='done')])
        old, new = definition('old', calls=calls), definition('new', calls=calls)
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_async_local_application('synthetic', tools=[old, new], initial_active_tools=['old'],
            system_prompt='BASE', response_format=None)
        sid = await app.sessions.create('A')
        async def change(number):
            if number == 1:
                await app.sessions.select_tools(sid, ['new'])
        model.on_request = change
        try:
            result = await app.runs.start(sid, [('user', 'run')])
            assert result['messages'][-1].content == 'done' and calls == [('old', 'value')]
            assert [[tool.name for tool in bound] for bound in model.bound] == [['old'], ['old'], ['new']]
            assert model.seen[0][0].content == model.seen[1][0].content
            assert inputs[0]['tools'][0] is old.tool
            assert inputs[0]['system_messages'][0].content == model.seen[0][0].content
        finally:
            await app.aclose()
    await scenario()


@async_test
async def test_concurrent_sessions_do_not_share_selected_tools(monkeypatch):
    async def scenario():
        both = asyncio.Event()
        class ConcurrentModel(DynamicModel):
            async def _agenerate(self, *args, **kwargs):
                result = await super()._agenerate(*args, **kwargs)
                if len(self.seen) == 2:
                    both.set()
                await asyncio.wait_for(both.wait(), 5)
                return result
        model = ConcurrentModel(responses=[AIMessage(content='done')])
        monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
        app = await build_async_local_application('synthetic', tools=[definition('first'), definition('second')],
            initial_active_tools=[], system_prompt='BASE', response_format=None)
        try:
            first, second = await app.sessions.create('A'), await app.sessions.create('A')
            await app.sessions.select_tools(first, ['first'])
            await app.sessions.select_tools(second, ['second'])
            await asyncio.wait_for(asyncio.gather(app.runs.start(first, [('user', 'one')]), app.runs.start(second, [('user', 'two')])), 10)
            assert sorted([[tool.name for tool in bound] for bound in model.bound]) == [['first'], ['second']]
            assert sorted('first:' in messages[0].content for messages in model.seen) == [False, True]
        finally:
            await app.aclose()
    await scenario()
