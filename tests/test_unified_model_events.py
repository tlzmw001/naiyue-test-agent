"""One callback event pair per execution; project acceptance stays separate."""

import json

import pytest

from native_application_helpers import async_test
from test_prompt_cache_call_paths import cached_application, completion_response


@async_test
async def test_single_callback_pair_uses_call_names_and_invocation_identity(monkeypatch, tmp_path):
    events, sent = [], []

    def serve(request):
        sent.append(json.loads(request.content))
        return completion_response(sent[-1], text='done')

    async with cached_application(monkeypatch, tmp_path, serve) as (app, _):
        app.observability.subscribe(events.append)
        result = await app.runs.start(await app.sessions.create('root'), [('user', 'task')])
    pair = [event for event in events if event.event_type.startswith('model_call_')]
    assert [event.event_type for event in pair] == ['model_call_started', 'model_call_finished']
    assert pair[0].data.invocation_id == pair[1].data.invocation_id
    assert pair[0].data.purpose == 'agent'
    assert pair[1].data.status == 'returned' and pair[1].data.usage_status == 'final'
    assert not any(event.event_type.startswith('model_invocation_') for event in events)
    assert len(sent) == 1 and result['messages'][-1].content == 'done'


@async_test
async def test_gate_rejection_keeps_one_returned_model_fact_and_failed_run(monkeypatch, tmp_path):
    from naiyue_test_agent.agent_runtime.errors import ModelResponseRejected
    events = []

    def serve(request):
        return completion_response(json.loads(request.content), call=('probe', '{bad', 'bad'))

    async with cached_application(monkeypatch, tmp_path, serve) as (app, _):
        app.observability.subscribe(events.append)
        with pytest.raises(ModelResponseRejected) as caught:
            await app.runs.start(await app.sessions.create('root'), [('user', 'task')])
    assert caught.value.reason == 'invalid_tool_calls'
    ends = [event for event in events if event.event_type == 'model_call_finished']
    assert len(ends) == 1 and ends[0].data.status == 'returned'
    assert ends[0].data.input_tokens == 100
    assert events[-1].event_type == 'run_finished' and events[-1].data.status == 'failed'
    assert events[-1].data.failure.reason == 'invalid_tool_calls'
    assert not any(event.event_type.startswith('model_invocation_') for event in events)


@pytest.mark.parametrize('purpose', ['agent', 'summary', 'auto_fast', 'auto_detailed', None])
def test_cli_model_notice_only_represents_agent_execution(purpose):
    from test_cli_event_display import display, event
    view, lines = display()
    view(event('model_call_started', 2, invocation_id='model-1', model_id='A', purpose=purpose))
    assert lines.count('模型正在处理请求') == (1 if purpose == 'agent' else 0)


def test_old_model_event_names_are_not_registered_aliases(caplog):
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher, ModelInvocationStartedData, _PAYLOADS
    events = []
    publisher = RunEventPublisher('s', 'r', (events.append,))
    assert 'model_invocation_started' not in _PAYLOADS and 'model_invocation_finished' not in _PAYLOADS
    publisher.publish('model_invocation_started', ModelInvocationStartedData('call', 'A', 'agent'))
    assert events == [] and 'event_payload_invalid' in caplog.text


def test_historical_diagnostic_names_and_payload_semantics_survive_reopen(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    from test_diagnostic_storage import fact, end, limits
    from test_model_usage_storage import seed
    path = tmp_path / 'history.sqlite'
    rows = []
    for sequence, (kind, data) in enumerate([
        ('model_call_finished', {'model_call_id': 'logical', 'status': 'accepted'}),
        ('model_invocation_finished', {'invocation_id': 'execution', 'status': 'returned'}),
    ], 2):
        row = fact(sequence=sequence)
        row.update(event_type=kind, data=data)
        rows.append(row)
    with ProjectStore(path) as store:
        seed(store)
        for row in rows:
            assert store.diagnostics.save_diagnostic_event(row, limits())
        store.diagnostics.finish_diagnostic_run(end(), limits())
    with ProjectStore(path) as store:
        events = store.diagnostics.read_diagnostic_events('s', 'r')['events']
        assert [(row['event_type'], row['data']) for row in events[:2]] == [
            (row['event_type'], row['data']) for row in rows]
