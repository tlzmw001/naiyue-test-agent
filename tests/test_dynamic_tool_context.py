"""Request selection stays aligned with compaction, retries and concurrent sessions."""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from naiyue_test_agent import application
from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.model_access import ModelCatalog
from test_async_context_chain import overflow_error
from test_dynamic_tools import DynamicModel, build, call, definition


@pytest.mark.parametrize('async_mode', [False, True])
def test_first_view_precedes_compaction_and_is_consumed_once(monkeypatch, async_mode):
    from naiyue_test_agent.agent_runtime import context_compaction as compaction
    from naiyue_test_agent.agent_runtime.tool_execution.toolset import ToolSetMiddleware
    trace, budgets = [], []
    resolve = application.SessionService.resolve_active_tool_names
    def selected(self, *args):
        trace.append('selection')
        return resolve(self, *args)
    monkeypatch.setattr(application.SessionService, 'resolve_active_tool_names', selected)
    def budget(**kwargs):
        trace.append('compaction')
        budgets.append(kwargs)
        return None
    async def abudget(**kwargs):
        return budget(**kwargs)
    monkeypatch.setattr(compaction, '_compact_messages', budget)
    monkeypatch.setattr(compaction, '_acompact_messages', abudget)
    before = ToolSetMiddleware.before_model
    def before_model(self, state, runtime):
        trace.append('before_model')
        return before(self, state, runtime)
    monkeypatch.setattr(ToolSetMiddleware, 'before_model', before_model)
    spec = definition(prompt_snippet='PROMPT_SNIPPET')
    model = DynamicModel(responses=[call(), AIMessage(content='done')])
    model.on_request = lambda _: trace.append('model')
    async def scenario():
        if async_mode:
            monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
            app = await build_async_local_application('synthetic', tools=[spec], system_prompt='BASE', response_format=None)
            sid = await app.acreate_session('A')
            try:
                await app.arun(sid, [('user', 'run')])
            finally:
                await app.aclose()
        else:
            app = build(monkeypatch, model, [spec], ['probe'])
            app.run(app.create_session('A'), [('user', 'run')])
    asyncio.run(scenario())
    assert trace == ['selection', 'compaction', 'before_model', 'model', 'before_model', 'selection', 'model']
    assert budgets[0]['tools'] == [spec.tool] and budgets[0]['tools'][0] is spec.tool
    assert budgets[0]['system_messages'][0].content == model.seen[0][0].content


@pytest.mark.parametrize('async_mode', [False, True])
def test_overflow_retry_keeps_view_then_next_request_refreshes(monkeypatch, async_mode):
    from naiyue_test_agent.agent_runtime import context_recovery as recovery
    inputs = []
    def compact(**kwargs):
        inputs.append(kwargs)
        return {'messages': [RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content='retained request')]}
    async def acompact(**kwargs):
        return compact(**kwargs)
    monkeypatch.setattr(recovery, '_compact_messages', compact)
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
        if async_mode:
            monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
            app = await build_async_local_application('synthetic', tools=[old, new], initial_active_tools=['old'],
                system_prompt='BASE', response_format=None)
            sid = await app.acreate_session('A')
            async def change(number):
                if number == 1:
                    await app.aset_active_tools(sid, ['new'])
            model.on_request = change
        else:
            app = build(monkeypatch, model, [old, new], ['old'])
            sid = app.create_session('A')
            model.on_request = lambda number: app.set_active_tools(sid, ['new']) if number == 1 else None
        try:
            result = await app.arun(sid, [('user', 'run')]) if async_mode else app.run(sid, [('user', 'run')])
            assert result['messages'][-1].content == 'done' and calls == [('old', 'value')]
            assert [[tool.name for tool in bound] for bound in model.bound] == [['old'], ['old'], ['new']]
            assert model.seen[0][0].content == model.seen[1][0].content
            assert inputs[0]['tools'][0] is old.tool
            assert inputs[0]['system_messages'][0].content == model.seen[0][0].content
        finally:
            if async_mode:
                await app.aclose()
    asyncio.run(scenario())


def test_concurrent_sessions_do_not_share_selected_tools(monkeypatch):
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
            first, second = await app.acreate_session('A'), await app.acreate_session('A')
            await app.aset_active_tools(first, ['first'])
            await app.aset_active_tools(second, ['second'])
            await asyncio.wait_for(asyncio.gather(app.arun(first, [('user', 'one')]), app.arun(second, [('user', 'two')])), 10)
            assert sorted([[tool.name for tool in bound] for bound in model.bound]) == [['first'], ['second']]
            assert sorted('first:' in messages[0].content for messages in model.seen) == [False, True]
        finally:
            await app.aclose()
    asyncio.run(scenario())
