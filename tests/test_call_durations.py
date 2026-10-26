"""Duration is optional observation of real terminal facts, never execution policy."""

from runtime_context_helpers import RunIdentity, RunObservationPorts, typed_context, typed_scope
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime import events as event_module
from naiyue_test_agent.agent_runtime.events import (
    RunEventPublisher, RunStartedData, RunFinishedData, ModelInvocationFinishedData, ToolCallFinishedData,
)
from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope
from naiyue_test_agent.agent_runtime.tool_execution.observation import aobserve_tool_dispatch
from naiyue_test_agent.application.observability.run_events import RunObservation, ApplicationEventSubscriptions
from naiyue_test_agent.application.observability.diagnostic_persistence import project_diagnostic_event
from naiyue_test_agent.web.serialization import wire_event
from test_model_invocation_observation import callback, metadata, output


TERMINALS = [lambda value: RunFinishedData('completed', duration_ms=value),
    lambda value: ModelInvocationFinishedData('call', 'returned', duration_ms=value),
    lambda value: ToolCallFinishedData('call', 'probe', 'dispatch', 'success', duration_ms=value)]


@pytest.mark.parametrize('factory', TERMINALS)
@pytest.mark.parametrize('value', [None, 0, 2**63 - 1])
def test_duration_contract_preserves_unknown_zero_and_maximum(factory, value):
    assert asdict(factory(value))['duration_ms'] == value


@pytest.mark.parametrize('factory', TERMINALS)
@pytest.mark.parametrize('value', [True, -1, 1.5, 2**63])
def test_duration_contract_rejects_invalid_values(factory, value):
    with pytest.raises(ValueError, match='duration'):
        factory(value)


@pytest.mark.parametrize(('stop', 'expected'), [(999999, 0), (8500000, 8), (-1, None), (2**63 * 1000000, None)])
def test_monotonic_duration_truncates_and_rejects_untrustworthy_delta(monkeypatch, caplog, stop, expected):
    from naiyue_test_agent.contracts import timing
    ticks = iter([0, stop])
    monkeypatch.setattr(timing, 'monotonic_ns', lambda: next(ticks))
    start = timing.start_timer()
    assert timing.elapsed_ms(start) == expected
    if expected is None:
        assert 'duration_observation_failed' in caplog.text


@pytest.mark.parametrize('status', ['returned', 'failed', 'cancelled'])
@async_test
async def test_model_duration_uses_callback_identity_and_monotonic_clock(monkeypatch, status):
    from naiyue_test_agent.contracts import timing
    tick = [0]
    monkeypatch.setattr(timing, 'monotonic_ns', lambda: tick[0])

    class JumpedUTC:
        count = 0
        @classmethod
        def now(cls, tz):
            cls.count += 1
            return datetime(2030 if cls.count == 1 else 2000, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(event_module, 'datetime', JumpedUTC)
    events, identity, handler = [], uuid4(), callback()
    with bind_run_scope(typed_scope(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
        tick[0] = 8_700_000
        if status == 'returned':
            await handler.on_llm_end(output(None), run_id=identity)
        else:
            error = asyncio.CancelledError() if status == 'cancelled' else RuntimeError('PRIVATE_BODY')
            await handler.on_llm_error(error, run_id=identity)
        await handler.on_llm_end(output(None), run_id=identity)
    assert len(events) == 2 and handler._active == {}
    assert events[0].timestamp > events[1].timestamp
    assert events[-1].data.duration_ms == 8 and events[-1].data.status == status
    assert 'PRIVATE_BODY' not in repr(events)


@pytest.mark.parametrize('fault_at', ['start', 'finish'])
@async_test
async def test_clock_failure_keeps_original_model_terminal(monkeypatch, caplog, fault_at):
    from naiyue_test_agent.contracts import timing
    calls = []
    def clock():
        calls.append(True)
        if len(calls) == (1 if fault_at == 'start' else 2):
            raise RuntimeError('PRIVATE_CLOCK')
        return 0
    monkeypatch.setattr(timing, 'monotonic_ns', clock)
    events, identity, handler = [], uuid4(), callback()
    with bind_run_scope(typed_scope(publisher=RunEventPublisher('s', 'r', (events.append,)))):
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata())
        await handler.on_llm_end(output(None), run_id=identity)
    assert len(events) == 2 and events[-1].data.status == 'returned'
    assert events[-1].data.duration_ms is None and handler._active == {}
    assert 'duration_observation_failed' in caplog.text and 'PRIVATE_CLOCK' not in caplog.text


@pytest.mark.parametrize('outcome', ['success', 'error_result', 'not_executed', 'exception', 'cancelled', 'bubble'])
@async_test
async def test_dispatch_duration_keeps_outcomes_and_control_contract(monkeypatch, outcome):
    from naiyue_test_agent.contracts import timing
    tick = [0]
    monkeypatch.setattr(timing, 'monotonic_ns', lambda: tick[0])
    events = []
    pub = RunEventPublisher('s', 'r', (events.append,))
    call = {'name': 'probe', 'id': 'call', 'args': {}}
    request = SimpleNamespace(tool_call=call, runtime=SimpleNamespace(context=typed_context(identity=RunIdentity(session_id='s', run_id='r'), observation=RunObservationPorts(publisher=pub))), state={
            'messages': [AIMessage(content='', tool_calls=[call])]})
    failure = (GraphBubbleUp() if outcome == 'bubble' else asyncio.CancelledError('PRIVATE')
               if outcome == 'cancelled' else RuntimeError('PRIVATE'))
    async def handler(_):
        if outcome in ('exception', 'cancelled', 'bubble'):
            raise failure
        return ToolMessage(content='body', tool_call_id='call', status='error' if outcome == 'error_result' else 'success')
    async def dispatch(enter):
        tick[0] = 12_800_000
        if outcome == 'not_executed':
            return ToolMessage(content='not executed', tool_call_id='call', status='error',
                additional_kwargs={'naiyue_not_executed': 'prior_batch_failed'})
        return await enter(request)
    if outcome in ('exception', 'cancelled', 'bubble'):
        with pytest.raises(type(failure)) as caught:
            await aobserve_tool_dispatch(request, handler, dispatch)
        assert caught.value is failure
    else:
        assert isinstance(await aobserve_tool_dispatch(request, handler, dispatch), ToolMessage)
    if outcome == 'bubble':
        assert [e.event_type for e in events] == ['tool_call_started']
    else:
        assert events[-1].data.duration_ms == 12
        assert events[-1].data.outcome == ('exception' if outcome == 'cancelled' else outcome)


@pytest.mark.parametrize('clock_fault', [False, True])
def test_run_duration_includes_finish_boundary_and_survives_clock_fault(monkeypatch, caplog, clock_fault):
    from naiyue_test_agent.contracts import timing
    tick = [0]
    def clock():
        if clock_fault:
            raise RuntimeError('PRIVATE_CLOCK')
        return tick[0]
    monkeypatch.setattr(timing, 'monotonic_ns', clock)
    events = []
    subscriptions = ApplicationEventSubscriptions()
    subscriptions.subscribe(events.append)
    observation = RunObservation(subscriptions.publisher('s', 'r'), 'message')
    observation.start()
    observation.result({}, None)
    tick[0] = 90_500_000  # The execution owner has now completed its required cleanup/save.
    with caplog.at_level('INFO'):
        observation.finish(False)
    end = events[-1]
    assert end.event_type == 'run_finished' and end.data.status == 'completed'
    assert end.data.duration_ms == (None if clock_fault else 90)
    assert wire_event(end)['data']['duration_ms'] == end.data.duration_ms
    assert project_diagnostic_event(end)['data']['duration_ms'] == end.data.duration_ms
    assert [r.application_event for r in caplog.records if hasattr(r, 'application_event')][-1]['duration_ms'] == end.data.duration_ms
    assert 'PRIVATE_CLOCK' not in caplog.text


def test_approval_resolution_duration_remains_unknown():
    assert ToolCallFinishedData('call', 'probe', 'approval_resolution', 'not_executed').duration_ms is None


@async_test
async def test_tool_clock_failure_preserves_original_business_error(monkeypatch, caplog):
    from naiyue_test_agent.contracts import timing
    def broken_clock():
        raise RuntimeError('PRIVATE_CLOCK')
    monkeypatch.setattr(timing, 'monotonic_ns', broken_clock)
    events, failure = [], RuntimeError('PRIVATE_BUSINESS')
    call = {'name': 'probe', 'id': 'call', 'args': {}}
    request = SimpleNamespace(tool_call=call, runtime=SimpleNamespace(context=typed_context(identity=RunIdentity(session_id='s', run_id='r'), observation=RunObservationPorts(publisher=RunEventPublisher('s', 'r', (events.append,))))),
        state={'messages': [AIMessage(content='', tool_calls=[call])]})
    async def handler(_):
        raise failure
    async def dispatch(enter):
        return await enter(request)
    with pytest.raises(RuntimeError) as caught:
        await aobserve_tool_dispatch(request, handler, dispatch)
    assert caught.value is failure
    assert len(events) == 2 and events[-1].data.outcome == 'exception'
    assert events[-1].data.duration_ms is None
    assert 'duration_observation_failed' in caplog.text and 'PRIVATE_' not in caplog.text + repr(events)


@pytest.mark.parametrize(('model_error', 'save_error'), [(False, False), (False, True), (True, False), (True, True)])
@async_test
async def test_root_duration_waits_for_required_save_and_preserves_failures(monkeypatch, tmp_path, model_error, save_error):
    from naiyue_test_agent.contracts import timing
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    from test_persistent_sessions import build
    from test_async_application import StreamingToolModel
    tick, events = [0], []
    monkeypatch.setattr(timing, 'monotonic_ns', lambda: tick[0])
    primary, secondary = TimeoutError('PRIVATE_MODEL'), SessionPersistenceError('safe')
    model = StreamingToolModel(replies=[primary if model_error else AIMessage(content='done')])
    entered, release = asyncio.Event(), asyncio.Event()
    async with build(monkeypatch, tmp_path / 'state.sqlite', model) as app:
        sid = await app.host.sessions.create('A')
        app.host.observability.subscribe(events.append)
        original = app.host.sessions._persistence.afinish
        async def finish(*args):
            entered.set()
            await release.wait()
            try:
                return await original(*args)
            finally:
                tick[0] = 86_900_000
        monkeypatch.setattr(app.host.sessions._persistence, 'afinish', finish)
        if save_error:
            def fail_save(*args, **kwargs):
                raise secondary
            monkeypatch.setattr(app.host.sessions._persistence.storage.runs, 'finish_run', fail_save)
        task = asyncio.create_task(app.host.runs.start(sid, [('user', 'task')]))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert not task.done() and not any(e.event_type == 'run_finished' for e in events)
            release.set()
            if model_error or save_error:
                with pytest.raises(type(primary if model_error else secondary)) as caught:
                    await task
                if model_error:
                    assert caught.value is primary
                else:
                    assert str(caught.value) == 'session_save_unconfirmed'
                    assert caught.value.__cause__ is secondary
            else:
                assert (await task)['messages'][-1].content == 'done'
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        end, = [e for e in events if e.event_type == 'run_finished']
        assert end.data.duration_ms == 86
        assert end.data.status == ('failed' if model_error or save_error else 'completed')
        assert not app.host.sessions._sessions.require(sid).describe_session()['busy']
