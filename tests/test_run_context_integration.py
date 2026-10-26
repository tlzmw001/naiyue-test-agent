"""Lifecycle references are checked through the actual application and graph."""

import asyncio

import pytest
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel

from native_application_helpers import async_test
from naiyue_test_agent.contracts.run_context import RuntimeContext
from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import ToolPermission
from naiyue_test_agent.tools.schemas import build_tool_schemas
from test_async_application import StreamingToolModel, make_app


@async_test
async def test_parallel_tools_share_one_plan_but_sessions_and_authorizations_are_isolated(monkeypatch):
    seen, release = [], asyncio.Event()
    public, execution = build_tool_schemas('ScopeProbe', BaseModel, slot=(str, ...))

    @tool(args_schema=execution)
    async def probe(slot: str, runtime: ToolRuntime[RuntimeContext]) -> str:
        """Read synthetic data while all four calls overlap."""
        context = runtime.context
        seen.append((context, context.invocation.tool_batch, current_run_scope()))
        if len(seen) == 4:
            release.set()
        await asyncio.wait_for(release.wait(), 5)
        return slot

    class StatelessModel(StreamingToolModel):
        async def _next(self, messages):
            self.seen.append(messages)
            if any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content='done')
            return AIMessage(content='', tool_calls=[
                {'name': 'probe', 'args': {'slot': slot}, 'id': slot} for slot in ('one', 'two')])

    model = StatelessModel(replies=[])
    app = await make_app(monkeypatch, model, [ToolSpec(probe, public,
        permissions=ToolPermission(default='allow'))])
    sessions = [await app.host.sessions.create('A') for _ in range(2)]
    try:
        results = await asyncio.gather(*(app.host.runs.start(sid, [('user', 'read both')]) for sid in sessions))
        assert all(result['messages'][-1].content == 'done' for result in results)
        groups = [[row for row in seen if row[0].scope.identity.session_id == sid] for sid in sessions]
        for first, second in groups:
            a, plan_a, bridge_a = first
            b, plan_b, bridge_b = second
            assert a.scope is b.scope is bridge_a is bridge_b
            assert a.invocation is b.invocation and plan_a is plan_b and plan_a is not None
            assert a is not b and a.authorization is not b.authorization
            assert a.authorization.permit is not b.authorization.permit
            assert {a.authorization.permit['call_id'], b.authorization.permit['call_id']} == {'one', 'two'}
        assert groups[0][0][0].scope is not groups[1][0][0].scope
        assert groups[0][0][0].invocation is not groups[1][0][0].invocation
        assert groups[0][0][1] is not groups[1][0][1]
        assert current_run_scope() is None
    finally:
        release.set()
        await app.aclose()


@async_test
async def test_approval_resume_starts_with_empty_model_state_and_no_reused_plan(monkeypatch):
    from naiyue_test_agent.agent_runtime import async_invocation
    from naiyue_test_agent.agent_runtime.tool_execution.batch import ToolBatchMiddleware
    contexts, batch_starts, executed = [], [], []
    original_context = async_invocation.make_invocation_context
    original_batch = ToolBatchMiddleware.aafter_model

    def record_context(*args, **kwargs):
        context = original_context(*args, **kwargs)
        contexts.append(context)
        assert context.invocation.model_call is None and context.invocation.tool_batch is None
        return context

    async def record_batch(self, state, runtime):
        batch_starts.append((runtime.context, runtime.context.invocation.model_call))
        return await original_batch(self, state, runtime)

    monkeypatch.setattr(async_invocation, 'make_invocation_context', record_context)
    monkeypatch.setattr(ToolBatchMiddleware, 'aafter_model', record_batch)

    public, execution = build_tool_schemas('ApprovalProbe', BaseModel)

    @tool(args_schema=execution)
    async def approved(runtime: ToolRuntime[RuntimeContext]) -> str:
        """Execute once after an explicit human approval."""
        executed.append(runtime.context)
        return 'approved'

    model = StreamingToolModel(replies=[AIMessage(content='', tool_calls=[
        {'name': 'approved', 'args': {}, 'id': 'one'}]), AIMessage(content='done')])
    app = await make_app(monkeypatch, model, [ToolSpec(approved, public,
        permissions=ToolPermission(default='ask'))])
    sid = await app.host.sessions.create('A')
    try:
        paused = await app.host.runs.start(sid, [('user', 'do it')])
        assert paused['approval_requests'] and len(model.seen) == 1 and executed == []
        result = await app.host.runs.resume_approval(sid, ['approve'])
        assert result['messages'][-1].content == 'done' and len(model.seen) == 2
        assert len(executed) == 1 and len(contexts) == 2
        assert contexts[0].invocation is not contexts[1].invocation
        resumed, model_state = batch_starts[0]
        assert resumed.scope is contexts[1].scope and model_state is None
        assert executed[0].scope is resumed.scope
    finally:
        await app.aclose()


@async_test
async def test_failed_next_request_clears_previous_preparation_and_batch():
    from types import SimpleNamespace
    from naiyue_test_agent.contracts.run_context import RunScope, RunIdentity
    from naiyue_test_agent.agent_runtime import make_model_selection_middleware
    model = StreamingToolModel(replies=[])
    calls = []
    failure = RuntimeError('synthetic preparation failure')

    def resolve(*args):
        calls.append(args)
        if len(calls) == 2:
            raise failure
        return 'A', model

    context = RuntimeContext(RunScope(RunIdentity('S', 'R')))
    runtime = SimpleNamespace(context=context)
    middleware = make_model_selection_middleware(resolve)
    await middleware.abefore_model({'messages': []}, runtime)
    previous = context.invocation.model_call
    assert previous.prepared is not None
    context.invocation.tool_batch = object()
    with pytest.raises(RuntimeError) as raised:
        await middleware.abefore_model({'messages': []}, runtime)
    assert raised.value is failure
    assert context.invocation.model_call is not previous
    assert context.invocation.model_call.prepared is None and context.invocation.tool_batch is None
