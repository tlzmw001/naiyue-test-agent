"""Approval, dispatch, guards, and batch skips never imply executor entry."""

from runtime_context_helpers import RunIdentity, RunObservationPorts, typed_context
from native_application_helpers import async_test

from naiyue_test_agent.tools.permissions import ToolPermission

from naiyue_test_agent.tools import ToolSpec

from threading import Barrier
from types import SimpleNamespace

import pytest
from tool_feedback_helpers import fail_tool_dispatch
from langchain.tools import tool
from langchain_core.messages import AIMessage, ToolMessage

from naiyue_test_agent.agent_runtime import _record_tool_call
from naiyue_test_agent.agent_runtime.events import RunStartedData, RunFinishedData
from naiyue_test_agent.tools import create_session_read_tool, create_session_write_tool
from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions
from test_application_run_events import ToolModel, make_app
from test_model_tool_subscriptions import events_of, CALL_METADATA_EVENTS


@pytest.mark.parametrize('decision', ['approve', 'reject'])
@async_test
async def test_approval_pause_and_resolution_have_accurate_boundaries(monkeypatch, tmp_path, decision):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('PRIVATE_BODY', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'approval-1'},
    ]), AIMessage(content='done')])
    app = await make_app(monkeypatch, model, [create_session_read_tool()])
    events = []
    app.subscribe_events(events.append)
    session = await app.acreate_workspace_session('A', root)
    await app.arun(session, [('user', 'read')])
    assert events[-1].data.status == 'interrupted'
    assert events_of(events, 'tool_call_started') == []
    initial_run = events[0].run_id
    result = await app.aresume_tool_approval(session, [decision])
    start, end = events_of(events, 'tool_call_started')[0], events_of(events, 'tool_call_finished')[0]
    assert start.run_id == end.run_id != initial_run
    assert start.data.tool_call_id == end.data.tool_call_id == 'approval-1'
    assert start.data.boundary == ('dispatch' if decision == 'approve' else 'approval_resolution')
    assert end.data.outcome == ('success' if decision == 'approve' else 'not_executed')
    assert end.data.reason == (None if decision == 'approve' else 'approval_rejected')
    message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert message.status == ('success' if decision == 'approve' else 'error')
    assert events[-1].data.status == 'completed'
    metadata = [e for e in events if e.event_type in CALL_METADATA_EVENTS]
    assert 'PRIVATE_BODY' not in repr(metadata)


@async_test
async def test_truncated_response_returns_original_error_result_without_tool_io(monkeypatch):
    called = []

    @tool
    def probe() -> str:
        """Record synthetic executor entry."""
        called.append(True)
        return 'unexpected'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'probe', 'args': {}, 'id': 'truncated-1'},
    ], response_metadata={'finish_reason': 'length'}), AIMessage(content='handled')])
    app = await make_app(monkeypatch, model, [ToolSpec(probe, probe.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    end = events_of(events, 'tool_call_finished')[0].data
    assert end.outcome == 'not_executed' and end.reason == 'model_response_truncated'
    assert called == []
    message = next(m for m in result['messages'] if isinstance(m, ToolMessage))
    assert message.status == 'error' and 'truncated' in message.content
    assert events[-1].data.status == 'completed'


@async_test
async def test_parameter_validation_is_error_result_not_executor_success(monkeypatch):
    called = []

    @tool
    def needs_value(value: str) -> str:
        """Require a value."""
        called.append(True)
        return value

    needs_value.handle_validation_error = 'Invalid arguments.'
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'needs_value', 'args': {}, 'id': 'invalid-1'},
    ]), AIMessage(content='handled')])
    app = await make_app(monkeypatch, model, [ToolSpec(needs_value, needs_value.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert called == []
    assert events_of(events, 'tool_call_finished')[0].data.outcome == 'error_result'
    assert next(m for m in result['messages'] if isinstance(m, ToolMessage)).status == 'error'


@async_test
async def test_fatal_sequential_tool_reports_later_batch_skip(monkeypatch):
    failure = RuntimeError('PRIVATE_FATAL')
    called = []

    @tool
    def first() -> str:
        """Fail the sequential batch."""
        return 'unreachable after the dispatch fault'


    @tool
    def later() -> str:
        """Must not run after a fatal error."""
        called.append(True)
        return 'unexpected'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'first', 'args': {}, 'id': 'first-1'},
        {'name': 'later', 'args': {}, 'id': 'later-1'},
    ])])
    fail_tool_dispatch(monkeypatch, 'first', failure)
    app = await make_app(monkeypatch, model, [ToolSpec(first, first.args_schema, execution_mode='sequential', permissions=ToolPermission(default='allow')), ToolSpec(later, later.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(RuntimeError) as caught:
        await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert caught.value is failure and called == []
    ends = {e.data.tool_call_id: e.data for e in events_of(events, 'tool_call_finished')}
    assert ends['first-1'].outcome == 'exception'
    assert ends['later-1'].outcome == 'not_executed' and ends['later-1'].reason == 'prior_batch_failed'
    assert events[-1].data.status == 'failed'


@async_test
async def test_parallel_tools_overlap_and_each_pair_has_order(monkeypatch):
    barrier = Barrier(2)

    @tool
    def parallel() -> str:
        """Wait for the other parallel call."""
        barrier.wait(timeout=3)
        return 'done'

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'parallel', 'args': {}, 'id': 'parallel-1'},
        {'name': 'parallel', 'args': {}, 'id': 'parallel-2'},
    ]), AIMessage(content='done')])
    app = await make_app(monkeypatch, model, [ToolSpec(parallel, parallel.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    for call_id in ('parallel-1', 'parallel-2'):
        pair = [e for e in events if e.event_type.startswith('tool_call_')
                and e.data.tool_call_id == call_id]
        assert [e.event_type for e in pair] == ['tool_call_started', 'tool_call_finished']
        assert pair[-1].data.outcome == 'success'


@async_test
async def test_unbound_tool_request_is_not_executed():
    @tool
    def probe() -> str:
        """Synthetic tool."""
        return 'unexpected'

    events = []
    subscriptions = ApplicationEventSubscriptions()
    subscriptions.subscribe(events.append)
    publisher = subscriptions.publisher('session', 'run')
    publisher.publish('run_started', RunStartedData('message'))
    request = SimpleNamespace(
        tool_call={'name': 'probe', 'args': {}, 'id': 'wrong'},
        runtime=SimpleNamespace(context=typed_context(identity=RunIdentity(session_id='session', run_id='run'), observation=RunObservationPorts(publisher=publisher))),
        state={'tool_execution': {'tool_names': ['probe']}, 'messages': [AIMessage(content='', tool_calls=[
            {'name': 'probe', 'args': {}, 'id': 'actual'},
        ])]},
    )
    called = []

    async def handler(req):
        async def async_handler_1(_r):
            return called.append(True)
        return (await _record_tool_call.awrap_tool_call(req, async_handler_1))

    from naiyue_test_agent.agent_runtime.tool_execution.observation import aobserve_tool_dispatch
    # The dispatch observer and inner guard own this mismatch contract;
    # batch-plan absence is checked separately by the scheduling tests.
    result = await aobserve_tool_dispatch(request, handler, lambda inner: inner(request))
    publisher.publish('run_finished', RunFinishedData('completed'))
    assert result.status == 'error' and called == []
    assert events_of(events, 'tool_call_finished')[0].data.reason == 'tool_call_mismatch'


@async_test
async def test_unconfirmed_process_cleanup_is_event_and_original_exception(monkeypatch):
    failure = ProcessCleanupError('PRIVATE_OUTCOME')

    @tool
    def uncertain() -> str:
        """Fail with unknown side effects."""
        raise failure

    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'uncertain', 'args': {}, 'id': 'uncertain-1'},
    ])])
    app = await make_app(monkeypatch, model, [ToolSpec(uncertain, uncertain.args_schema, permissions=ToolPermission(default='allow'))])
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(ProcessCleanupError) as caught:
        await app.arun(await app.acreate_session('A'), [('user', 'run')])
    assert caught.value is failure
    assert events_of(events, 'tool_call_finished')[0].data.outcome == 'outcome_unknown'
    assert events[-1].data.manual_review_required is True


@async_test
async def test_mixed_approval_decisions_report_each_call_once(monkeypatch, tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.md'
    outside.write_text('data', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'approve-1'},
        {'name': 'read', 'args': {'path': str(outside)}, 'id': 'reject-1'},
    ]), AIMessage(content='done')])
    app = await make_app(monkeypatch, model, [create_session_read_tool()])
    events = []
    app.subscribe_events(events.append)
    session = await app.acreate_workspace_session('A', root)
    await app.arun(session, [('user', 'read')])
    await app.aresume_tool_approval(session, ['approve', 'reject'])
    ends = {e.data.tool_call_id: e.data for e in events_of(events, 'tool_call_finished')}
    assert len(events_of(events, 'tool_call_finished')) == 2
    assert ends['approve-1'].outcome == 'success' and ends['approve-1'].boundary == 'dispatch'
    assert ends['reject-1'].reason == 'approval_rejected'


@async_test
async def test_real_read_write_read_batch_keeps_execution_order(monkeypatch, tmp_path):
    path = tmp_path / 'sample.txt'
    path.write_text('before', encoding='utf-8')
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'read', 'args': {'path': str(path)}, 'id': 'read-before'},
        {'name': 'write', 'args': {'path': str(path), 'content': 'after'}, 'id': 'write'},
        {'name': 'read', 'args': {'path': str(path)}, 'id': 'read-after'},
    ]), AIMessage(content='done')])
    app = await make_app(monkeypatch, model, [create_session_read_tool(), create_session_write_tool()])
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_workspace_session('A', tmp_path), [('user', 'run batch')])
    outputs = {m.tool_call_id: m for m in result['messages'] if isinstance(m, ToolMessage)}
    assert outputs['read-before'].content == 'before' and outputs['read-after'].content == 'after'
    assert path.read_text() == 'after'
    assert [e.data.tool_call_id for e in events_of(events, 'tool_call_finished')] == [
        'read-before', 'write', 'read-after',
    ]
