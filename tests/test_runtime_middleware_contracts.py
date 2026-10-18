"""Observe real production hooks and wrappers without replacing their behavior."""

import asyncio
from functools import wraps

import pytest
from langchain.agents.middleware import ToolErrorMiddleware
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException
from pydantic import BaseModel

from native_application_helpers import async_test, build_application
from naiyue_test_agent import application
from naiyue_test_agent.agent_runtime import tool_error_feedback
from naiyue_test_agent.agent_runtime.tool_execution.approval import BatchApprovalMiddleware
from naiyue_test_agent.agent_runtime.tool_execution.authorization import ExecutionAuthorizationMiddleware
from naiyue_test_agent.agent_runtime.tool_execution.batch import ToolBatchMiddleware
from naiyue_test_agent.agent_runtime.tool_execution.observation import _record_tool_call
from naiyue_test_agent.agent_runtime.tool_execution.permissions import PermissionMiddleware
from naiyue_test_agent.agent_runtime.tool_execution.toolset import ToolSetMiddleware
from naiyue_test_agent.agent_runtime.turn_observation import TurnBoundaryMiddleware
from naiyue_test_agent.model_access import ModelCatalog, MODEL_ID_METADATA_KEY
from naiyue_test_agent.tools import ToolSpec
from naiyue_test_agent.tools.permissions import PermissionMechanismError, ToolPermission, require_tool_permit
from naiyue_test_agent.tools.schemas import build_tool_schemas
from test_dynamic_tools import DynamicModel, definition


@pytest.fixture
def middleware_trace(monkeypatch):
    # Local instrumentation delegates once to each original method and copies
    # hook_config metadata. Assertions stay outside optional observer callbacks.
    trace = []

    def after_hook(cls, label):
        original = cls.aafter_model

        @wraps(original)
        async def record(self, state, runtime):
            latest = next(m for m in reversed(state['messages']) if isinstance(m, AIMessage))
            key = (runtime.context.get('agent_path', 'root'), latest.id)
            trace.append((label + '.enter', *key, None))
            result = await original(self, state, runtime)
            calls = (result or {}).get('tool_execution', {}).get('calls', [])
            trace.append((label + '.exit', *key, [(c['id'], c['decision']) for c in calls]))
            return result

        monkeypatch.setattr(cls, 'aafter_model', record)

    for cls, label in ((PermissionMiddleware, 'permission'), (BatchApprovalMiddleware, 'approval'),
                       (ToolBatchMiddleware, 'plan')):
        after_hook(cls, label)

    def tool_wrapper(target, label):
        original = target.awrap_tool_call

        @wraps(original)
        async def record(*args):
            # Class methods receive self; the shared observation instance may
            # have a bound method restored by an earlier fault-injection test.
            request, handler = args[-2:]
            key = (request.runtime.context.get('agent_path', 'root'), request.tool_call['id'])
            trace.append((label + '.enter', *key, None))
            try:
                result = await original(*args)
            except BaseException as error:
                trace.append((label + '.raise', *key, error))
                raise
            trace.append((label + '.exit', *key, None))
            return result

        monkeypatch.setattr(target, 'awrap_tool_call', record)

    for target, label in ((ToolSetMiddleware, 'toolset'), (ToolBatchMiddleware, 'dispatch'),
                          (_record_tool_call, 'observation'), (ToolErrorMiddleware, 'errors')):
        tool_wrapper(target, label)

    ready = TurnBoundaryMiddleware.after_model

    @wraps(ready)
    def mark_ready(self, state, runtime):
        result = ready(self, state, runtime)
        latest = next(m for m in reversed(state['messages']) if isinstance(m, AIMessage))
        turns = runtime.context['_turn_observer']
        trace.append(('ready', runtime.context.get('agent_path', 'root'), latest.id,
                      (turns._ready, turns._phase)))
        return result

    monkeypatch.setattr(TurnBoundaryMiddleware, 'after_model', mark_ready)
    authorize = ExecutionAuthorizationMiddleware._authorize

    @wraps(authorize)
    def permit(self, request):
        result = authorize(self, request)
        binding = None if isinstance(result, ToolMessage) else result.runtime.context.get('_execution_permit')
        trace.append(('authorize', request.runtime.context.get('agent_path', 'root'), request.tool_call['id'], binding))
        return result

    monkeypatch.setattr(ExecutionAuthorizationMiddleware, '_authorize', permit)
    return trace


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@async_test
async def test_production_batch_prepares_before_approval_and_executes_only_after_resume(
    monkeypatch, middleware_trace, decision, caplog,
):
    trace = middleware_trace
    effects = []
    model = DynamicModel(responses=[AIMessage(id='batch', content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 'ask'}, 'id': 'ask'},
        {'name': 'ordinary', 'args': {'value': 'allow'}, 'id': 'ordinary'},
    ]), AIMessage(id='answer', content='done')], metadata={MODEL_ID_METADATA_KEY: 'A'})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[definition(calls=effects, approved=True, sequential=True),
        definition('ordinary', calls=effects)], system_prompt='', response_format=None)
    events = []
    app.subscribe_events(events.append)
    sid = await app.acreate_session('A')
    paused = await app.arun(sid, [('user', 'run both')])
    assert [r['tool_call_id'] for r in paused['approval_requests']] == ['ask']
    assert effects == [] and len(model.seen) == 1
    assert [row[0] for row in trace] == ['permission.enter', 'permission.exit', 'approval.enter']
    assert trace[1][3] == [('ask', 'ask'), ('ordinary', 'allow')]
    assert not any(isinstance(m, ToolMessage) for m in paused['messages'])
    assert [e.event_type for e in events if e.event_type.startswith('turn_')] == ['turn_started', 'turn_suspended']

    result = await app.aresume_tool_approval(sid, [decision])
    batch = [row for row in trace if row[2] == 'batch']
    assert [row[0] for row in batch] == ['permission.enter', 'permission.exit', 'approval.enter',
        'approval.enter', 'approval.exit', 'plan.enter', 'plan.exit', 'ready']
    assert batch[-1][3] == (True, 'active')  # Check completion is separate from tool result pairing.
    assert sum(row[0] == 'permission.enter' and row[2] == 'batch' for row in trace) == 1
    executed = ['ask', 'ordinary'] if decision == 'approve' else ['ordinary']
    assert effects == ([('probe', 'ask'), ('ordinary', 'allow')] if decision == 'approve' else [('ordinary', 'allow')])
    for call_id in executed:
        rows = [row for row in trace if row[2] == call_id]
        assert [row[0] for row in rows] == ['toolset.enter', 'dispatch.enter', 'observation.enter',
            'errors.enter', 'authorize', 'errors.exit', 'observation.exit', 'dispatch.exit', 'toolset.exit']
        assert rows[4][3]['call_id'] == call_id and rows[4][3]['batch_id'] == 'batch'
        assert trace.index(batch[-1]) < trace.index(rows[0])
    if decision == 'reject':
        assert not any(row[2] == 'ask' for row in trace)
    feedback = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.status) for m in feedback] == [
        ('ask', 'success' if decision == 'approve' else 'error'), ('ordinary', 'success')]
    assert result['messages'][-1].content == 'done' and len(model.seen) == 2
    finishes = [e for e in events if e.event_type == 'turn_finished']
    assert len(finishes) == 2 and all(e.data.status == 'completed' for e in finishes)
    assert finishes[0].data.tool_call_ids == ('ask', 'ordinary')
    assert finishes[0].data.tool_message_ids == tuple(m.id for m in feedback)
    next_model = [e for e in events if e.event_type == 'model_call_started'][1]
    assert finishes[0].sequence < next_model.sequence
    assert not any('observation_failed' in row.getMessage() or 'unavailable' in row.getMessage()
                   for row in caplog.records)


@pytest.mark.parametrize('mechanism', [False, True], ids=['tool-feedback', 'mechanism-propagates'])
@async_test
async def test_real_authorization_precedes_tool_and_error_boundary_preserves_failure_kind(
    monkeypatch, middleware_trace, mechanism, caplog,
):
    trace = middleware_trace
    failure = PermissionMechanismError('PRIVATE_FAILURE') if mechanism else ToolException('Choose a valid target.')

    public, execution = build_tool_schemas('BoundaryProbeInput', BaseModel)

    @tool(args_schema=execution)
    def probe(runtime: ToolRuntime[dict, dict]) -> str:
        """Fail after checking the actual execution permit."""
        binding = require_tool_permit(runtime)
        trace.append(('body', 'root', runtime.tool_call_id, binding))
        raise failure

    import naiyue_test_agent.agent_runtime as runtime_module
    conversions = []

    def feedback(error, request):
        conversions.append((error, request.tool_call['id']))
        return tool_error_feedback(error, request)

    monkeypatch.setattr(runtime_module, 'tool_error_feedback', feedback)
    model = DynamicModel(responses=[AIMessage(id='batch', content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'call'}]), AIMessage(content='handled')])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[ToolSpec(probe, public,
        permissions=ToolPermission(default='allow'))], system_prompt='', response_format=None)
    events = []
    app.subscribe_events(events.append)
    sid = await app.acreate_session('A')
    if mechanism:
        with pytest.raises(PermissionMechanismError) as caught:
            await app.arun(sid, [('user', 'run')])
        assert caught.value is failure and len(model.seen) == 1
        assert [e.data.status for e in events if e.event_type == 'turn_finished'] == ['failed']
    else:
        result = await app.arun(sid, [('user', 'run')])
        output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        assert (output.tool_call_id, output.status, output.content) == ('call', 'error', str(failure))
        assert result['messages'][-1].content == 'handled' and len(model.seen) == 2
    calls = [row for row in trace if row[2] == 'call']
    assert [row[0] for row in calls[:6]] == ['toolset.enter', 'dispatch.enter', 'observation.enter',
                                           'errors.enter', 'authorize', 'body']
    assert calls[4][3] == calls[5][3] and calls[4][3]['call_id'] == 'call'
    assert conversions == [(failure, 'call')]
    assert [row[0] for row in calls[6:]] == [label + ('.raise' if mechanism else '.exit')
        for label in ('errors', 'observation', 'dispatch', 'toolset')]
    if mechanism:
        assert all(row[3] is failure for row in calls[6:])
    assert not any('observation_failed' in row.getMessage() for row in caplog.records)


@async_test
async def test_request_observer_sees_selected_model_tools_prompt_and_adapted_messages(monkeypatch, tmp_path, caplog):
    from naiyue_test_agent.agent_runtime.context_observation import observe_model_request

    default = DynamicModel(responses=[], profile={'image_inputs': True})
    selected = DynamicModel(responses=[AIMessage(content='done')], profile={'image_inputs': False})
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': default, 'B': selected}))
    requests = []
    cls = type(observe_model_request)
    original = cls.awrap_model_call

    @wraps(original)
    async def capture(self, request, handler):
        requests.append((request.model, [t.name for t in request.tools], request.system_message.content,
                         [(m.type, m.content) for m in request.messages]))
        return await original(self, request, handler)

    monkeypatch.setattr(cls, 'awrap_model_call', capture)
    (tmp_path / 'AGENTS.md').write_text('PROJECT_CONSTRAINT', encoding='utf-8')
    app = await build_application('unused', tools=[definition(), definition('unused')],
        initial_active_tools=['unused'], system_prompt='BASE_PROMPT', response_format=None)
    sid = await app.acreate_workspace_session('A', tmp_path)
    await app.aselect_model(sid, 'B')
    await app.aset_active_tools(sid, ['probe'])
    canonical = [{'type': 'text', 'text': 'request'},
                 {'type': 'image', 'source_type': 'base64', 'data': 'PRIVATE_PIXELS', 'mime_type': 'image/png'}]
    result = await app.arun(sid, [HumanMessage(content=canonical)])
    observed_model, tools, prompt, messages = requests[0]
    assert len(requests) == 1 and observed_model is selected and default.seen == []
    assert tools == [t.name for t in selected.bound[0]] == ['probe']
    assert 'BASE_PROMPT' in prompt and 'PROJECT_CONSTRAINT' in prompt
    assert 'probe:' in prompt and 'unused:' not in prompt
    assert [('system', prompt), *messages] == [(m.type, m.content) for m in selected.seen[0]]
    assert messages == [('human', [{'type': 'text', 'text': 'request'}, {'type': 'text',
        'text': '[Image pixels not provided: current model does not support image input.]'}])]
    assert result['messages'][0].content == canonical and 'PRIVATE_PIXELS' not in caplog.text
    assert result['messages'][-1].content == 'done'
    assert not any('observation_failed' in row.getMessage() or 'context_snapshot_unavailable' in row.getMessage()
                   for row in caplog.records)


@async_test
async def test_stop_after_model_response_jumps_past_permission_approval_plan_and_tool(monkeypatch, middleware_trace):
    from naiyue_test_agent.agent_runtime.call_observation import _ModelSelectionMiddleware

    model = DynamicModel(responses=[AIMessage(id='batch', content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 'must not run'}, 'id': 'stopped'}])])
    effects = []
    original = _ModelSelectionMiddleware._accept

    @wraps(original)
    def stop_after_accept(self, request, response, model_id, call_id):
        result = original(self, request, response, model_id, call_id)
        app.request_stop(sid)
        return result

    monkeypatch.setattr(_ModelSelectionMiddleware, '_accept', stop_after_accept)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))
    app = await build_application('unused', tools=[definition(calls=effects, approved=True)],
                                  system_prompt='', response_format=None)
    sid = await app.acreate_session('A')
    result = await app.arun(sid, [('user', 'run')])
    assert result['run_stopped'] and '__interrupt__' not in result
    assert middleware_trace == [] and effects == [] and len(model.seen) == 1
    assert app.pending_tool_approvals(sid) == []
    output, = [m for m in result['messages'] if isinstance(m, ToolMessage)]
    assert (output.tool_call_id, output.status, output.additional_kwargs['naiyue_not_executed']) == (
        'stopped', 'error', 'user_stop')


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@async_test
async def test_child_graph_uses_same_permission_approval_plan_and_authorization_order(
    monkeypatch, middleware_trace, decision, caplog,
):
    from test_subagent_execution import ChildModel, delegated_app, role
    from test_subagent_approval_resume import approval_notifications, cancel_run, next_approval, submit

    effects = []
    child = ChildModel(replies=[AIMessage(id='child-batch', content='', tool_calls=[
        {'name': 'probe', 'args': {'value': 'ask'}, 'id': 'ask'},
        {'name': 'ordinary', 'args': {'value': 'allow'}, 'id': 'ordinary'},
    ]), AIMessage(content='child report')])
    app, root = await delegated_app(monkeypatch, child, spec=role(tool_names=('probe', 'ordinary')),
        tools=[definition(calls=effects, approved=True, sequential=True), definition('ordinary', calls=effects)])
    # These direct fake catalogs bypass _build_model; supply its identity metadata.
    root.metadata = {**(root.metadata or {}), MODEL_ID_METADATA_KEY: 'A'}
    child.metadata = {**(child.metadata or {}), MODEL_ID_METADATA_KEY: 'B'}
    queue = approval_notifications(monkeypatch, app)
    sid = await app.acreate_session('A')
    task = asyncio.create_task(app.arun(sid, [('user', 'delegate')]))
    try:
        batch = await next_approval(app, sid, queue)
        path = batch['agent_path']
        before = [row for row in middleware_trace if row[1] == path]
        assert path.startswith('root/tester/') and effects == [] and not task.done()
        assert [row[0] for row in before] == ['permission.enter', 'permission.exit', 'approval.enter']
        assert before[1][3] == [('ask', 'ask'), ('ordinary', 'allow')]
        await submit(app, sid, batch, [decision])
        result = await asyncio.wait_for(task, 5)
        trace = [row for row in middleware_trace if row[1] == path]
        assert [row[0] for row in trace if row[2] == 'child-batch'] == ['permission.enter', 'permission.exit',
            'approval.enter', 'approval.enter', 'approval.exit', 'plan.enter', 'plan.exit', 'ready']
        expected = ['ask', 'ordinary'] if decision == 'approve' else ['ordinary']
        for call_id in expected:
            calls = [row for row in trace if row[2] == call_id]
            assert [row[0] for row in calls] == ['toolset.enter', 'dispatch.enter', 'observation.enter',
                'errors.enter', 'authorize', 'errors.exit', 'observation.exit', 'dispatch.exit', 'toolset.exit']
            assert calls[4][3]['agent_path'] == path and calls[4][3]['call_id'] == call_id
        if decision == 'reject':
            assert not any(row[2] == 'ask' for row in trace)
        feedback = [m for m in child.seen[-1] if isinstance(m, ToolMessage)]
        assert [(m.tool_call_id, m.status) for m in feedback] == [
            ('ask', 'success' if decision == 'approve' else 'error'), ('ordinary', 'success')]
        assert effects == ([('probe', 'ask'), ('ordinary', 'allow')] if decision == 'approve' else [('ordinary', 'allow')])
        assert len(child.seen) == len(root.seen) == 2
        assert result['messages'][-1].content == 'root done' and app.pending_subagent_approvals(sid) == []
        assert not any('observation_failed' in row.getMessage() or 'unavailable' in row.getMessage()
                       for row in caplog.records)
    finally:
        await cancel_run(task)
