"""Memory uses existing model facts and a separate observed execution."""

from runtime_context_helpers import identity_for, typed_scope
from uuid import uuid4
import asyncio

import pytest

from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.agent_runtime.model_usage_observation import ModelUsageCallback
from naiyue_test_agent.agent_runtime.run_scope import bind_run_scope
from naiyue_test_agent.application.observability.run_events import RunObservation
from naiyue_test_agent.storage.session_storage import SessionStorage
from native_application_helpers import async_test
from test_model_invocation_observation import metadata, output
from test_model_usage_storage import seed


@async_test
@pytest.mark.parametrize('failed', [False, True])
async def test_memory_invocation_keeps_existing_pair_and_independent_run(tmp_path, caplog, failed):
    events, identity = [], uuid4()
    publisher = RunEventPublisher('s', 'memory-run', (events.append,))
    observation = RunObservation(publisher, mode='memory')
    observation.start()
    with observation.bind(identity=identity_for(observation.publisher)):
        handler = ModelUsageCallback()
        await handler.on_chat_model_start({}, [['PRIVATE_PROMPT']], run_id=identity,
            metadata=metadata(), tags=['naiyue_memory'])
        if failed:
            error = RuntimeError('PRIVATE_RESPONSE')
            await handler.on_llm_error(error, run_id=identity)
            observation.failed(error, stage='execute')
        else:
            await handler.on_llm_end(output(None), run_id=identity)
    observation.finish(False)
    assert [event.event_type for event in events] == [
        'run_started', 'model_call_started', 'model_call_finished', 'run_finished']
    assert events[0].data.mode == 'memory'
    start, finish = events[1:3]
    assert start.data.purpose == 'memory'
    assert start.data.invocation_id == finish.data.invocation_id == str(identity)
    assert finish.data.status == ('failed' if failed else 'returned')
    assert finish.data.usage_status == 'unavailable' and finish.data.input_tokens is None
    assert {event.run_id for event in events} == {'memory-run'}
    assert handler._active == {}
    assert 'PRIVATE' not in repr(events) + caplog.text

    from naiyue_test_agent.application.observability.diagnostic_persistence import project_diagnostic_event
    from naiyue_test_agent.settings import DiagnosticSettings
    path, settings = tmp_path / 'state.sqlite', DiagnosticSettings()
    with SessionStorage(path) as store:
        seed(store)
        before = store.get('s')
        for event in events:
            if event.event_type.startswith('model_call_'):
                store.save_model_usage_event(event)
            store.save_diagnostic_event(project_diagnostic_event(event), settings)
        store.finish_model_usage_run('s', 'memory-run')
        store.finish_diagnostic_run(project_diagnostic_event(events[-1]), settings)
        assert store.get('s') == before
    with SessionStorage(path) as store:
        usage = store.read_model_usage('s', model_limit=20)
        assert usage['recorded_calls'] == 1
        activity, = usage['activities']
        assert activity['id'] == 'memory'
        assert activity['metrics']['input_tokens']['known_sum'] is None
        detail = store.read_diagnostic_events('s', 'memory-run')
        assert detail['summary']['mode'] == 'memory'
        assert detail['events'][1]['data']['purpose'] == 'memory'


@async_test
async def test_conflicting_memory_tag_does_not_fabricate_a_purpose(caplog):
    from naiyue_test_agent.agent_runtime.stream_events import MEMORY_TAG, SUMMARY_TAG
    events, identity = [], uuid4()
    with bind_run_scope(typed_scope(publisher=RunEventPublisher('s', 'memory-run', (events.append,)))):
        handler = ModelUsageCallback()
        await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata(),
            tags=[MEMORY_TAG, SUMMARY_TAG])
        await handler.on_llm_end(output(None), run_id=identity)
    assert events[0].data.purpose is None
    assert 'model_usage_purpose_unavailable' in caplog.text


def test_cli_memory_purpose_and_execution_mode_are_named():
    from naiyue_test_agent.cli.cli_observation import _event_line, _run_lines, _usage_lines
    from test_cli_observation import usage, summary
    value = usage()
    value['activities'][0]['id'] = 'memory'
    assert '记忆整理' in '\n'.join(_usage_lines(value))
    run = summary()
    run['mode'] = 'memory'
    assert '记忆整理' in '\n'.join(_run_lines(run))
    assert '用途 记忆整理' in _event_line({'sequence': 1, 'event_type': 'model_call_started',
        'data': {'purpose': 'memory'}, 'source': None})


@async_test
async def test_cancelled_memory_call_returns_to_parent_observation_without_reusing_root_id():
    from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
    from naiyue_test_agent.agent_runtime.stream_events import MEMORY_TAG
    root_events, memory_events = [], []
    root = typed_scope(publisher=RunEventPublisher('s', 'root-run', (root_events.append,)))
    observation = RunObservation(RunEventPublisher('s', 'memory-run', (memory_events.append,)), mode='memory')
    with bind_run_scope(root):
        observation.start()
        with observation.bind(identity=identity_for(observation.publisher)):
            handler, identity = ModelUsageCallback(), uuid4()
            await handler.on_chat_model_start({}, [], run_id=identity, metadata=metadata(), tags=[MEMORY_TAG])
            await handler.on_llm_error(asyncio.CancelledError(), run_id=identity)
            observation.status = 'stopped'
        assert current_run_scope() is root
        observation.finish(False)
    assert root_events == []
    assert memory_events[-2].data.status == 'cancelled'
    assert memory_events[-1].data.status == 'stopped'
    assert memory_events[-2].data.usage_status == 'unavailable'
    assert memory_events[-1].data.stop_mode == 'active'
    assert handler._active == {}


@pytest.mark.parametrize('origin', ['', 'PRIVATE\nFORGED', 'x' * 129, 1, True])
def test_memory_run_origin_rejects_invalid_correlation_identifiers(origin):
    from naiyue_test_agent.contracts.session_events import RunStartedData
    with pytest.raises(ValueError, match='origin'):
        RunStartedData('memory', origin_run_id=origin)


def test_memory_origin_is_persisted_without_requiring_it_on_older_summaries(tmp_path):
    from naiyue_test_agent.contracts.session_events import RunStartedData, RunFinishedData
    from naiyue_test_agent.application.observability.diagnostic_persistence import project_diagnostic_event
    from naiyue_test_agent.settings import DiagnosticSettings
    from naiyue_test_agent.application.observability import diagnostic_storage
    import json

    path, settings = tmp_path / 'state.sqlite', DiagnosticSettings()
    with SessionStorage(path) as store:
        seed(store)
        events = []
        publisher = RunEventPublisher('s', 'memory-run', (events.append,))
        publisher.publish('run_started', RunStartedData('memory', origin_run_id='root-run'))
        publisher.publish('run_finished', RunFinishedData('completed'))
        for event in events:
            store.save_diagnostic_event(project_diagnostic_event(event), settings)
        store.finish_diagnostic_run(project_diagnostic_event(events[-1]), settings)
        value = store.read_diagnostic_events('s', 'memory-run')
        assert value['summary']['origin_run_id'] == 'root-run'
        assert value['events'][0]['data']['origin_run_id'] == 'root-run'
        with store.connection:
            row = store.connection.execute('SELECT * FROM naiyue_diagnostic_runs').fetchone()
            old = json.loads(row['summary_json'])
            old.pop('origin_run_id')
            diagnostic_storage._update(store.connection, row, old, closed_at=row['closed_at'])
    with SessionStorage(path) as store:
        summary = store.read_diagnostic_events('s', 'memory-run')['summary']
        assert summary['mode'] == 'memory' and 'origin_run_id' not in summary
    assert RunStartedData('message').origin_run_id is None
