"""Failure, fallback, and observation limits keep original execution behavior."""

import asyncio

from native_application_helpers import async_test, build_application

from threading import Barrier

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.agent_runtime.context_compaction import ContextCompactionError
from naiyue_test_agent.agent_runtime.events import _PAYLOADS
from test_context_compaction import _app, _model
from test_context_operation_events import OP_TYPES, operation_events, prepared_app
from test_context_overflow_recovery import _configured_model, _history, _status


def test_only_four_public_types_are_added_for_context_operations():
    registered = {name for name in _PAYLOADS if name.startswith(('compaction_', 'context_recovery_'))}
    from naiyue_test_agent.agent_runtime.stream_events import MessageDeltaData, ToolProgressData
    assert registered == OP_TYPES and len(_PAYLOADS) == 31
    from naiyue_test_agent.agent_runtime.events import ModelInvocationStartedData, ModelInvocationFinishedData
    assert _PAYLOADS['model_invocation_started'] is ModelInvocationStartedData
    assert _PAYLOADS['model_invocation_finished'] is ModelInvocationFinishedData
    assert _PAYLOADS['message_delta'] is MessageDeltaData
    assert _PAYLOADS['tool_progress'] is ToolProgressData
    from naiyue_test_agent.agent_runtime.context_usage import ModelContextSnapshotData
    assert _PAYLOADS['model_context_snapshot'] is ModelContextSnapshotData


@async_test
async def test_soft_trigger_without_cut_is_skipped_and_original_request_runs(monkeypatch):
    model = _model([AIMessage(content='done')], window=1000, output=20, reserve=950, keep=100)
    app = await _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), [('user', 'x' * 300)])
    operations = operation_events(events)
    assert [e.event_type for e in operations] == ['compaction_started', 'compaction_finished']
    assert operations[-1].data.status == 'skipped'
    assert operations[-1].data.reason == 'no_safe_cut_within_send_budget'
    assert result['messages'][0].content == 'x' * 300
    assert len(model.normal_inputs) == 1 and model.summary_inputs == []


@async_test
async def test_forced_summary_failure_does_not_fall_back_or_retry_normal_request(monkeypatch):
    model = _configured_model([_status(), AIMessage(content='must not run')],
                              summaries=[TimeoutError('PRIVATE_SUMMARY')])
    app = await _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(ContextCompactionError) as caught:
        await app.arun(await app.acreate_session('A'), _history())
    assert str(caught.value) == 'summary_failed'
    compaction = next(e.data for e in events if e.event_type == 'compaction_finished')
    recovery = next(e.data for e in events if e.event_type == 'context_recovery_finished')
    assert compaction.status == 'failed' and compaction.error_type == 'TimeoutError'
    assert recovery.status == 'failed' and recovery.stage == 'compact' and recovery.retry_attempts == 0
    assert len(model.normal_inputs) == len(model.summary_inputs) == 1
    assert 'PRIVATE_' not in repr(operation_events(events))


@async_test
async def test_retry_exception_keeps_identity_and_summary_is_not_applied(monkeypatch):
    failure = TimeoutError('PRIVATE_RETRY')
    model = _configured_model([_status(), failure])
    app = await _app(monkeypatch, {'A': model})
    events = []
    app.subscribe_events(events.append)
    with pytest.raises(TimeoutError) as caught:
        await app.arun(await app.acreate_session('A'), _history())
    assert caught.value is failure
    compaction = next(e.data for e in events if e.event_type == 'compaction_finished')
    recovery = next(e.data for e in events if e.event_type == 'context_recovery_finished')
    assert compaction.status == 'unconfirmed' and compaction.summary_message_id is None
    assert recovery.status == 'failed' and recovery.stage == 'retry' and recovery.retry_attempts == 1
    assert events[-1].data.status == 'failed' and len(model.normal_inputs) == 2


@async_test
async def test_post_summary_budget_failure_has_no_applied_event_or_normal_request(monkeypatch):
    app, session, model, events = await prepared_app(monkeypatch, AIMessage(content='x' * 5000))
    with pytest.raises(ContextCompactionError) as caught:
        await app.arun(session, [('user', 'third')])
    assert str(caught.value) == 'compacted_request_over_budget'
    assert operation_events(events)[-1].data.status == 'failed'
    assert operation_events(events)[-1].data.reason == 'compacted_request_over_budget'
    assert model.normal_inputs == [] and events[-1].data.status == 'failed'


@async_test
async def test_custom_invoke_result_is_preserved_but_replacement_is_unconfirmed(monkeypatch):
    app, session, model, events = await prepared_app(monkeypatch)
    original = app._agent.ainvoke

    async def custom(input, **kwargs):
        return await original(input, **kwargs)

    monkeypatch.setattr(app._agent, 'ainvoke', custom)
    result = await app.arun(session, [('user', 'third')])
    assert result['messages'][0].additional_kwargs['naiyue_compaction_summary'] is True
    end = operation_events(events)[-1].data
    assert end.status == 'unconfirmed' and end.reason == 'state_replacement_unconfirmed'
    assert end.summary_message_id is None and end.visibility is None
    assert len(model.summary_inputs) == len(model.normal_inputs) == 1


@async_test
async def test_context_consumer_failure_is_isolated_and_real_replacement_still_observed(monkeypatch, caplog):
    app, session, model, events = await prepared_app(monkeypatch)
    calls = []

    def broken(event):
        if event.event_type in OP_TYPES:
            calls.append(event.event_type)
            raise RuntimeError('PRIVATE_CONSUMER')

    app.subscribe_events(broken)
    result = await app.arun(session, [('user', 'third')])
    assert calls == ['compaction_started']
    assert result['messages'][-1].content == 'final'
    assert operation_events(events)[-1].data.status == 'applied'
    assert 'event_observer_failed' in caplog.text and 'PRIVATE_' not in caplog.text


@async_test
async def test_context_publish_fault_does_not_change_compaction_or_model_result(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher

    app, session, model, events = await prepared_app(monkeypatch)
    original = RunEventPublisher.publish

    def fail_context(self, event_type, data):
        if event_type in OP_TYPES:
            raise RuntimeError('PRIVATE_PUBLISH')
        return original(self, event_type, data)

    monkeypatch.setattr(RunEventPublisher, 'publish', fail_context)
    result = await app.arun(session, [('user', 'third')])
    assert result['messages'][0].additional_kwargs['naiyue_compaction_summary'] is True
    assert result['messages'][-1].content == 'final'
    assert events[-1].data.status == 'completed'
    assert 'context_event_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text


@async_test
async def test_context_failure_reporting_error_never_masks_original_exception(monkeypatch, caplog):
    from naiyue_test_agent.agent_runtime.context_operation_observation import ContextOperationObserver

    failure = TimeoutError('PRIVATE_PRIMARY')
    model = _configured_model([_status(), failure])
    app = await _app(monkeypatch, {'A': model})
    original = ContextOperationObserver.finalize

    def fail_report(self, error=None):
        if error is not None:
            raise ValueError('PRIVATE_SECONDARY')
        return original(self, error)

    monkeypatch.setattr(ContextOperationObserver, 'finalize', fail_report)
    with pytest.raises(TimeoutError) as caught:
        await app.arun(await app.acreate_session('A'), _history())
    assert caught.value is failure
    assert 'context_failure_publish_failed' in caplog.text and 'PRIVATE_' not in caplog.text


@async_test
async def test_recovery_operations_are_isolated_between_sessions(monkeypatch):
    from langchain_core.outputs import ChatGeneration, ChatResult
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog
    from test_context_compaction import RoutedFake

    barrier = Barrier(2)

    class Concurrent(RoutedFake):
        def _generate(self, messages, *args, **kwargs):
            if str(messages[0].content).startswith('Summarize completed'):
                barrier.wait(timeout=5)
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='brief'))])
            if any(m.additional_kwargs.get('naiyue_compaction_summary') for m in messages):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='done'))])
            raise _status()

    model = Concurrent(responses=[AIMessage(content='unused')], summary_responses=[],
                       metadata=_configured_model([]).metadata)
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    app = await build_application('synthetic.json', tools=[], system_prompt='BASE', response_format=None)
    sessions = [await app.acreate_session('A') for _ in range(2)]
    events, outcomes = [], []
    app.subscribe_events(events.append)

    async def run(session):
        try:
            outcomes.append(await app.arun(session, _history()))
        except BaseException as error:
            outcomes.append(error)

    workers = [asyncio.create_task(run(session)) for session in sessions]
    for worker in workers:
        await asyncio.wait_for(asyncio.shield(worker), 6)
        assert worker.done()
    assert len(outcomes) == 2 and all(isinstance(r, dict) for r in outcomes), outcomes
    assert len({e.data.operation_id for e in events if e.event_type == 'compaction_started'}) == 2
    for session in sessions:
        scoped = [e for e in events if e.session_id == session]
        ops = operation_events(scoped)
        assert [e.event_type for e in ops] == [
            'context_recovery_started', 'compaction_started', 'compaction_finished', 'context_recovery_finished',
        ]
        assert ops[-1].data.status == 'recovered'
        assert [e.sequence for e in scoped] == list(range(1, len(scoped) + 1))


@async_test
async def test_structured_retry_response_keeps_object_and_confirms_context_state(monkeypatch):
    from langchain.agents.structured_output import ToolStrategy
    from pydantic import BaseModel
    from naiyue_test_agent import application
    from naiyue_test_agent.model_access import ModelCatalog

    class Answer(BaseModel):
        value: str

    model = _configured_model([_status(), AIMessage(content='', tool_calls=[
        {'name': 'Answer', 'args': {'value': 'structured'}, 'id': 'schema'},
    ])])
    monkeypatch.setattr(application, 'load_model_catalog', lambda _p: ModelCatalog('A', {'A': model}))
    app = await build_application('synthetic.json', tools=[], system_prompt='BASE',
                                               response_format=ToolStrategy(Answer))
    events = []
    app.subscribe_events(events.append)
    result = await app.arun(await app.acreate_session('A'), _history())
    assert isinstance(result['structured_response'], Answer) and result['structured_response'].value == 'structured'
    assert operation_events(events)[-1].data.status == 'recovered'
    assert result['messages'][-1].type == 'tool' and result['messages'][-1].tool_call_id == 'schema'
    assert len(model.normal_inputs) == 2 and len(model.summary_inputs) == 1
