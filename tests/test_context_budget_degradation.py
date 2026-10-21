"""Unknown estimates never erase known limits or required preparation failures."""

import json
import logging
import asyncio

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from event_sequence import EventSequenceChecker
from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime import context_compaction, context_usage
from naiyue_test_agent.agent_runtime.context_compaction import ContextCompactionError, SUMMARY_MARKER, SUMMARY_PREFIX
from naiyue_test_agent.model_access import MODEL_ID_METADATA_KEY
from naiyue_test_agent import application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application
from naiyue_test_agent.model_access import ModelCatalog
from test_context_compaction import _app, _model
from test_context_inspector import model_with_limits
from test_context_overflow_recovery import _configured_model, _history, _status
from test_prompt_cache_call_paths import cached_application, completion_response
from test_dynamic_tools import definition


def test_composition_failure_preserves_total_images_and_serializable_snapshot(monkeypatch, caplog):
    messages = [HumanMessage(content=[{'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,PRIVATE_PIXELS'}}])]
    system = SystemMessage(content='PRIVATE_SYSTEM')
    total = count_tokens_approximately([system, *messages])

    def fail(*args):
        raise RuntimeError('PRIVATE_COMPOSITION')

    monkeypatch.setattr(context_usage, '_system_children', fail)
    estimate = context_usage.estimate_context(model_with_limits([]), system, messages, [])
    assert estimate['status'] == 'available' and estimate['estimated_input_tokens'] == total
    assert estimate['groups_status'] == 'unavailable' and estimate['groups'] == []
    assert estimate['image_count'] == 1
    snapshot = context_usage.request_snapshot({'_naiyue_model_call_id': 'call', '_naiyue_model_id': 'A'}, estimate)
    assert context_usage.read_snapshot(snapshot) == snapshot
    context_usage.ModelContextSnapshotData('call', json.dumps(snapshot))
    assert 'PRIVATE_' not in json.dumps(snapshot) + caplog.text


def test_total_failure_preserves_unknown_instead_of_zero(monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE_COUNTER')

    monkeypatch.setattr(context_usage, 'count_tokens_approximately', fail)
    estimate = context_usage.estimate_context(model_with_limits([]), SystemMessage(content='base'), [], [])
    assert estimate['status'] == estimate['groups_status'] == 'unavailable'
    assert estimate['estimated_input_tokens'] is None and estimate['groups'] == []
    assert 'context_total_estimate_unavailable' in caplog.text and 'PRIVATE_COUNTER' not in caplog.text


def test_old_snapshot_without_group_status_is_read_without_rewriting():
    estimate = context_usage.estimate_context(model_with_limits([]), SystemMessage(content='base'), [], [])
    estimate.pop('groups_status', None)
    snapshot = context_usage.request_snapshot({'_naiyue_model_call_id': 'call', '_naiyue_model_id': 'A'}, estimate)
    assert context_usage.read_snapshot(snapshot) == snapshot
    assert 'groups_status' not in snapshot['estimate']


@pytest.mark.parametrize('change', [
    {'groups_status': 'unavailable'},
    {'groups_status': 'invalid'},
    {'status': 'unavailable', 'groups_status': 'available'},
    {'groups_status': 'available', 'estimated_input_tokens': None},
])
def test_snapshot_rejects_inconsistent_total_and_composition(change):
    estimate = context_usage.estimate_context(model_with_limits([]), SystemMessage(content='base'), [], [])
    estimate.update(change)
    snapshot = context_usage.request_snapshot({'_naiyue_model_call_id': 'call', '_naiyue_model_id': 'A'}, estimate)
    assert context_usage.read_snapshot(snapshot) is None


@pytest.mark.parametrize('reason', ['missing_budget_metadata', 'initial_estimate_unavailable'])
@async_test
async def test_ordinary_unknown_budget_sends_original_with_explicit_diagnostic(monkeypatch, caplog, reason):
    model = _model([AIMessage(content='done')], window=10000)
    model.metadata[MODEL_ID_METADATA_KEY] = 'A'
    if reason == 'missing_budget_metadata':
        model.metadata.pop('naiyue_model_limits')
    else:
        def fail(*args, **kwargs):
            raise RuntimeError('PRIVATE_ESTIMATE')
        monkeypatch.setattr(context_compaction, 'count_tokens_approximately', fail)
    app = await _app(monkeypatch, {'A': model})
    events = []
    try:
        sid = await app.acreate_session('A')
        app.subscribe_events(events.append)
        with caplog.at_level(logging.INFO):
            result = await app.arun(sid, [('user', 'original input')])
        assert result['messages'][-1].content == 'done'
        assert len(model.normal_inputs) == 1 and not model.summary_inputs
        assert model.normal_inputs[0][-1].content == 'original input'
        assert len([e for e in events if e.event_type == 'model_call_started']) == 1
        diagnostics = [r.context_preparation for r in caplog.records if hasattr(r, 'context_preparation')]
        assert len(diagnostics) == 1
        assert diagnostics[0]['event'] == 'context_budget_unverified'
        assert diagnostics[0]['reason'] == reason and diagnostics[0]['session_id'] == sid
        assert diagnostics[0]['model_id'] == 'A' and diagnostics[0]['run_id'] == events[0].run_id
        assert 'PRIVATE_' not in caplog.text + repr(diagnostics)
        EventSequenceChecker.assert_healthy(events, caplog.records)
    finally:
        await app.aclose()


@pytest.mark.parametrize(('section', 'field', 'value'), [
    ('naiyue_model_limits', 'max_output_tokens', 10000),
    ('naiyue_model_limits', 'context_window_tokens', False),
    ('naiyue_model_limits', 'context_window_tokens', 'PRIVATE_INVALID'),
    ('naiyue_compaction_reserve', 'tokens', 0),
])
@async_test
async def test_invalid_budget_is_required_failure_even_when_estimator_is_unavailable(
    monkeypatch, caplog, section, field, value,
):
    model = _model([AIMessage(content='must not run')], window=10000)
    model.metadata[section][field] = value
    counted = []

    def fail(*args, **kwargs):
        counted.append(True)
        raise RuntimeError('PRIVATE_COUNTER')

    monkeypatch.setattr(context_compaction, 'count_tokens_approximately', fail)
    app = await _app(monkeypatch, {'A': model})
    events = []
    try:
        sid = await app.acreate_session('A')
        app.subscribe_events(events.append)
        with pytest.raises(ValueError, match='compaction'):
            await app.arun(sid, [('user', 'prepare')])
        assert not counted and not model.normal_inputs and not model.summary_inputs
        assert not any(e.event_type == 'model_call_started' for e in events)
        assert events[-1].data.status == 'failed' and not app._sessions.require(sid).is_busy()
        assert not any(hasattr(r, 'context_preparation') for r in caplog.records)
        assert 'PRIVATE_' not in caplog.text
        EventSequenceChecker.assert_healthy(events, caplog.records)
    finally:
        await app.aclose()


@async_test
async def test_required_input_conversion_is_not_skipped_for_missing_budget(monkeypatch):
    failure = ValueError('PRIVATE_INPUT')
    model = _model([], window=10000)
    model.metadata.pop('naiyue_model_limits')

    def fail(*args):
        raise failure

    monkeypatch.setattr(context_compaction, 'prepare_model_messages', fail)
    with pytest.raises(ValueError) as caught:
        await context_compaction._acompact_messages([HumanMessage(content='input')], model,
            model_id='A', context={}, system_messages=[], tools=[])
    assert caught.value is failure and not model.normal_inputs and not model.summary_inputs


@pytest.mark.parametrize('stage', ['candidate', 'post_summary'])
@async_test
async def test_known_over_budget_cannot_fall_back_when_candidate_estimate_fails(monkeypatch, caplog, stage):
    messages = [HumanMessage(content='old ' * 400), AIMessage(content='old answer'), HumanMessage(content='latest')]
    model = _model([AIMessage(content='must not run')], window=1200, output=900, reserve=900,
        summaries=[AIMessage(content='brief')])
    assert count_tokens_approximately([SystemMessage(content='BASE'), *messages]) > 300
    failure = RuntimeError('PRIVATE_CANDIDATE')
    count = context_compaction.count_tokens_approximately

    def checked(messages, **kwargs):
        if any(isinstance(m, HumanMessage) and str(m.content).startswith(SUMMARY_PREFIX)
               and bool(m.additional_kwargs.get(SUMMARY_MARKER)) == (stage == 'post_summary') for m in messages):
            raise failure
        return count(messages, **kwargs)

    monkeypatch.setattr(context_compaction, 'count_tokens_approximately', checked)
    app = await _app(monkeypatch, {'A': model})
    try:
        sid = await app.acreate_session('A')
        with pytest.raises(ContextCompactionError) as caught:
            await app.arun(sid, messages)
        assert caught.value.__cause__ is failure
        assert not model.normal_inputs and len(model.summary_inputs) == (stage == 'post_summary')
        saved = await app._agent.aget_state({'configurable': {'thread_id': app._sessions.require(sid).get_thread_id()}})
        assert [m.content for m in saved.values['messages']] == [m.content for m in messages]
        assert not any(m.additional_kwargs.get(SUMMARY_MARKER) for m in saved.values['messages'])
        assert 'context_budget_unverified' not in caplog.text and 'PRIVATE_' not in caplog.text
    finally:
        await app.aclose()


@async_test
async def test_explicit_overflow_with_unknown_estimate_never_retries_unchanged_input(monkeypatch, caplog):
    model = _configured_model([_status(), AIMessage(content='must not retry')])

    def fail(*args, **kwargs):
        raise RuntimeError('PRIVATE_COUNTER')

    monkeypatch.setattr(context_compaction, 'count_tokens_approximately', fail)
    app = await _app(monkeypatch, {'A': model})
    try:
        sid = await app.acreate_session('A')
        with pytest.raises(ContextCompactionError, match='compaction_unavailable'):
            await app.arun(sid, _history())
        assert len(model.normal_inputs) == 1 and not model.summary_inputs
        assert 'context_overflow_retry_started' not in caplog.text
        assert not app._sessions.require(sid).is_busy()
    finally:
        await app.aclose()


@async_test
async def test_total_only_snapshot_survives_persistent_query_and_reopen(monkeypatch, tmp_path, caplog):
    model = _model([AIMessage(content='done')], window=10000)
    model.metadata[MODEL_ID_METADATA_KEY] = 'A'
    monkeypatch.setattr(application, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': model}))

    def fail(*args):
        raise RuntimeError('PRIVATE_COMPOSITION')

    monkeypatch.setattr(context_usage, '_system_children', fail)
    options = {'database_path': tmp_path / 'state.sqlite', 'tools': [], 'system_prompt': 'BASE', 'response_format': None}
    async with open_async_persistent_application('unused', **options) as app:
        sid = await app.acreate_session('A')
        events = []
        app.subscribe_events(events.append)
        await app.arun(sid, [('user', 'input')])
        view = await app.aget_context_view(sid)
        saved = view['latest_request']
        assert saved['estimate']['status'] == 'available'
        assert saved['estimate']['estimated_input_tokens'] > 0
        assert saved['estimate']['groups_status'] == 'unavailable' and saved['estimate']['groups'] == []
        assert 'PRIVATE_' not in repr(saved) + caplog.text
        EventSequenceChecker.assert_healthy(events, caplog.records)
    async with open_async_persistent_application('unused', **options) as app:
        assert (await app.aget_context_view(sid))['latest_request'] == saved
        assert len(model.normal_inputs) == 1


@async_test
async def test_overflow_recovery_keeps_request_configuration_and_never_replays_prior_tool(monkeypatch, tmp_path, caplog):
    entered, release = asyncio.Event(), asyncio.Event()
    sent, effects, events = [], [], []
    normal_calls = 0

    async def serve(request):
        nonlocal normal_calls
        body = json.loads(request.content)
        sent.append(body)
        if body['messages'][0]['content'].startswith('Summarize completed'):
            entered.set()
            await release.wait()
            return completion_response(body, text='Earlier facts and the tool result were recorded.')
        normal_calls += 1
        if normal_calls == 1:
            return completion_response(body, call=('first', {'value': 'once'}, 'once'))
        if normal_calls == 2:
            return httpx.Response(400, json={'error': {'code': 'context_length_exceeded', 'message': 'input too long'}})
        return completion_response(body, text='done')

    async with cached_application(monkeypatch, tmp_path, serve,
            tools=[definition('first', calls=effects), definition('second')]) as (app, catalog):
        catalog.get('root').metadata['naiyue_compaction_keep_recent'] = {'tokens': 8, 'source': 'config'}
        sid = await app.acreate_session('root')
        await app.aselect_thinking(sid, 'root', 'low')
        await app.aset_active_tools(sid, ['first'])
        app.subscribe_events(events.append)
        task = asyncio.create_task(app.arun(sid, _history()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await app.aselect_model(sid, 'child')
            await app.aset_active_tools(sid, ['second'])
        finally:
            release.set()
        result = await task
        assert result['messages'][-1].content == 'done'
        assert result['messages'][0].additional_kwargs[SUMMARY_MARKER] is True
        await app.arun(sid, [('user', 'next request')])
    assert effects == [('first', 'once')] and normal_calls == 4
    assert [body['model'] for body in sent] == ['root', 'root', 'root', 'root', 'child']
    assert all(body['reasoning_effort'] == 'low' for body in sent[:-1])
    assert 'reasoning_effort' not in sent[-1]
    normal = [body for body in sent if not body['messages'][0]['content'].startswith('Summarize completed')]
    assert [[t['function']['name'] for t in body['tools']] for body in normal] == [['first']] * 3 + [['second']]
    assert normal[0]['messages'][0] == normal[1]['messages'][0] == normal[2]['messages'][0]
    first_run = [e for e in events if e.run_id == events[0].run_id]
    assert [e.data.purpose for e in first_run if e.event_type == 'model_call_started'] == ['agent', 'agent', 'summary', 'agent']
    recovery = next(e.data for e in first_run if e.event_type == 'context_recovery_finished')
    assert recovery.status == 'recovered' and recovery.retry_attempts == 1
    for rid in {e.run_id for e in events}:
        EventSequenceChecker.assert_healthy([e for e in events if e.run_id == rid], caplog.records)
