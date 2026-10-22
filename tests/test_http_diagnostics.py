"""HTTP facts use existing bounded run diagnostics, including unknown history."""

import pytest
from dataclasses import replace

from naiyue_test_agent.agent_runtime import events as facts
from naiyue_test_agent.application.diagnostic_persistence import project_diagnostic_event
from naiyue_test_agent.application.session_storage import SessionStorage
from test_model_usage_storage import seed
from test_diagnostic_storage import limits


@pytest.mark.parametrize('value', [True, 0, 2, '1'])
def test_run_capture_version_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        facts.RunStartedData('message', http_observation_version=value)


@pytest.mark.parametrize('value', [True, 'partial', 'complete ', 1])
def test_model_capture_status_is_a_finite_contract(value):
    with pytest.raises(ValueError):
        facts.ModelInvocationFinishedData('i', 'returned', http_capture_status=value)


def recorded_events(*, version=1, model=True, status='complete'):
    result = []
    pub = facts.RunEventPublisher('s', 'r', (result.append,))
    pub.publish('run_started', facts.RunStartedData('message', http_observation_version=version))
    if model:
        pub.publish('model_call_started', facts.ModelInvocationStartedData('i', 'A', 'agent'))
        pub.publish('model_http_attempt_started', facts.ModelHttpAttemptStartedData('a', 'i'))
        pub.publish('model_http_attempt_finished', facts.ModelHttpAttemptFinishedData('a', 'i', 'response_headers', 503, 7))
        pub.publish('model_call_finished', facts.ModelInvocationFinishedData('i', 'returned', http_capture_status=status))
    pub.publish('run_finished', facts.RunFinishedData('completed'))
    return result


@pytest.mark.parametrize(('version', 'model', 'status', 'expected'), [
    (1, True, 'complete', {'status': 'complete', 'observed_attempts': 1, 'response_headers': 1, 'unconfirmed': 0}),
    (1, False, 'complete', {'status': 'complete', 'observed_attempts': 0, 'response_headers': 0, 'unconfirmed': 0}),
    (None, False, 'complete', {'status': 'unavailable', 'observed_attempts': None, 'response_headers': None, 'unconfirmed': None}),
    (1, True, 'incomplete', {'status': 'partial', 'observed_attempts': 1, 'response_headers': 1, 'unconfirmed': 0}),
    (1, True, None, {'status': 'partial', 'observed_attempts': 1, 'response_headers': 1, 'unconfirmed': 0}),
])
def test_http_summary_reopens_is_read_only_and_independent_of_page(tmp_path, version, model, status, expected):
    events = recorded_events(version=version, model=model, status=status)
    path = tmp_path / 'state.sqlite'
    with SessionStorage(path) as store:
        seed(store)
        for event in events[:-1]:
            record = project_diagnostic_event(event)
            assert record is not None
            assert store.save_diagnostic_event(record, limits())
            assert store.save_diagnostic_event(record, limits())  # Same sequence never counts twice.
        store.finish_diagnostic_run(project_diagnostic_event(events[-1]), limits())
        assert store.read_diagnostic_runs('s')['runs'][0]['http_attempts'] == expected
        writes = store.connection.total_changes
        assert store.read_diagnostic_events('s', 'r', limit=1)['summary']['http_attempts'] == expected
        assert store.connection.total_changes == writes
    with SessionStorage(path) as store:
        assert store.read_diagnostic_runs('s')['runs'][0]['http_attempts'] == expected
        assert store.connection.execute('SELECT version FROM naiyue_store_info').fetchone()[0] == 14


def test_diagnostic_loss_and_missing_terminal_cannot_report_complete(tmp_path):
    events = recorded_events()
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        for event in events[:-1]:
            store.save_diagnostic_event(project_diagnostic_event(event), limits())
        store.finish_diagnostic_run(project_diagnostic_event(events[-1]), limits(), reason='buffer_overflow', dropped_events=1)
        summary = store.read_diagnostic_runs('s')['runs'][0]
        assert summary['capture_incomplete'] and summary['http_attempts']['status'] == 'partial'


def test_projector_keeps_only_safe_http_fact_fields():
    events = recorded_events()
    start = project_diagnostic_event(events[1])
    request = project_diagnostic_event(events[2])
    response = project_diagnostic_event(events[3])
    assert start['data']['invocation_id'] == request['data']['invocation_id'] == 'i'
    assert request['data'] == {'invocation_id': 'i', 'attempt_id': 'a'}
    assert response['data'] == {'status': 'response_headers', 'invocation_id': 'i', 'attempt_id': 'a',
                                'http_status': 503, 'response_headers_ms': 7}


def test_orphan_request_gap_is_exposed_even_with_no_attempt_events(tmp_path):
    events = recorded_events(model=False)
    events[-1] = replace(events[-1], data=facts.RunFinishedData('completed', http_capture_status='incomplete'))
    with SessionStorage(tmp_path / 'state.sqlite') as store:
        seed(store)
        store.save_diagnostic_event(project_diagnostic_event(events[0]), limits())
        store.finish_diagnostic_run(project_diagnostic_event(events[-1]), limits())
        assert store.read_diagnostic_runs('s')['runs'][0]['http_attempts'] == {
            'status': 'partial', 'observed_attempts': 0, 'response_headers': 0, 'unconfirmed': 0}


def test_event_checker_rejects_http_completion_after_model_terminal():
    from event_sequence import CheckedPublisher
    pub = CheckedPublisher('s', 'r', ())
    pub.publish('run_started', facts.RunStartedData('message'))
    pub.publish('model_call_started', facts.ModelInvocationStartedData('i', 'A', 'agent'))
    pub.publish('model_http_attempt_started', facts.ModelHttpAttemptStartedData('a', 'i'))
    with pytest.raises(ValueError, match='unfinished HTTP'):
        pub.publish('model_call_finished', facts.ModelInvocationFinishedData('i', 'returned'))
    pub.publish('model_http_attempt_finished', facts.ModelHttpAttemptFinishedData('a', 'i', 'unconfirmed'))
    pub.publish('model_call_finished', facts.ModelInvocationFinishedData('i', 'returned'))
    with pytest.raises(ValueError, match='does not match'):
        pub.publish('model_http_attempt_finished', facts.ModelHttpAttemptFinishedData('a', 'i', 'unconfirmed'))
