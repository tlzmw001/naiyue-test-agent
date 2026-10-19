"""Byte eviction affects replay cursors, never the live run or execution."""

import asyncio
from dataclasses import replace
import json

import pytest
from langchain_core.messages import AIMessage

from native_application_helpers import async_test
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData, RunFinishedData
from naiyue_test_agent.agent_runtime.stream_events import MessageDeltaData
from naiyue_test_agent.web.delivered_run import DeliveredRun
from naiyue_test_agent.web.errors import WebError
from naiyue_test_agent.web.serialization import encoded_size
from naiyue_test_agent.web.settings import WebSettings


def new_job(**limits):
    settings = WebSettings(database_path='unused', frontend_dist='unused', **limits)
    job = DeliveredRun('session', [], settings, lambda _: None)
    events = []
    publisher = RunEventPublisher('session', 'run', (job.observe, events.append))
    publisher.publish('run_started', RunStartedData('message'))
    return job, publisher, events


def delta(publisher, text='内容', index=1):
    publisher.publish('message_delta', MessageDeltaData('message', text, index))


@async_test
async def test_default_budget_and_exact_utf8_boundary_then_evict_oldest():
    job, publisher, events = new_job()
    assert job.settings.event_cache_bytes == 4 * 1024 * 1024
    delta(publisher, text='你好🙂')
    exact = sum(encoded_size(item) for item in job.events)
    assert job.event_bytes == exact
    assert exact > sum(len(json.dumps(item, ensure_ascii=False, separators=(',', ':'))) for item in job.events)
    settings = job.settings.model_copy(update={'event_cache_bytes': exact})
    job = DeliveredRun('session', [], settings, lambda _: None)
    for event in events:
        job.observe(event)
    assert job.event_bytes == exact and len(job.events) == 2
    job.check_cursor(0)
    delta(publisher, text='继续', index=2)
    job.observe(events[-1])
    assert job.event_bytes <= exact
    assert job.events[-1]['sequence'] == 3 and job.events[0]['sequence'] > 1
    assert job.event_bytes == sum(encoded_size(item) for item in job.events)
    with pytest.raises(WebError, match='resync_required'):
        job.check_cursor(0)
    job.check_cursor(job.events[0]['sequence'] - 1)
    assert job.view()['messages'][0]['content'] == '你好🙂继续'
    assert not job.view()['incomplete']


@pytest.mark.parametrize('capacity, budget', [(2, 10000), (256, 1), (2, 600)])
@async_test
async def test_count_and_bytes_remain_consistent_across_repeated_evictions(capacity, budget):
    job, publisher, _ = new_job(event_capacity=capacity, event_cache_bytes=budget)
    for index in range(1, 16):
        delta(publisher, text='x' * index, index=index)
        assert len(job.events) <= capacity
        assert job.event_bytes <= budget
        assert job.event_bytes == sum(encoded_size(item) for item in job.events)
    job.check_cursor(job.last_sequence)
    with pytest.raises(WebError, match='resync_required'):
        job.check_cursor(0)
    assert not job.view()['incomplete']


@async_test
async def test_event_larger_than_replay_budget_updates_view_and_empty_queue_rejects_old_cursor():
    job, publisher, _ = new_job(event_cache_bytes=1)
    delta(publisher, text='完整消息')
    assert list(job.events) == [] and job.event_bytes == 0
    assert job.view()['messages'][0]['content'] == '完整消息'
    assert not job.view()['incomplete']
    assert [frame async for frame in job.stream(0)] == [
        {'event': 'resync_required', 'data': {'code': 'resync_required'}}]
    with pytest.raises(WebError, match='resync_required'):
        job.check_cursor(job.last_sequence - 1)
    job.check_cursor(job.last_sequence)
    job.done = True
    assert [frame async for frame in job.stream(job.last_sequence)] == []


@async_test
async def test_replay_resumes_after_oversized_event_and_preserves_terminal_state():
    job, publisher, _ = new_job(event_cache_bytes=600)
    delta(publisher, text='x' * 2000)
    assert not job.events
    discarded = job.last_sequence
    publisher.publish('run_finished', RunFinishedData('completed'))
    job.done = True
    assert job.view()['status'] == 'completed'
    assert job.view()['messages'][0]['content'] == 'x' * 2000
    assert job.view()['incomplete'] is False
    frames = [frame async for frame in job.stream(discarded)]
    assert len(frames) == 1 and frames[0]['data']['event_type'] == 'run_finished'
    assert job.event_bytes == encoded_size(job.events[0])


@async_test
async def test_foreign_duplicate_and_old_events_do_not_change_replay_accounting():
    job, publisher, events = new_job(event_cache_bytes=1000)
    delta(publisher)
    original = (job.event_bytes, list(job.events), job.last_sequence)
    for event in (events[-1], replace(events[-1], session_id='other'),
                  replace(events[-1], run_id='other'), replace(events[-1], sequence=1)):
        job.observe(event)
        assert (job.event_bytes, list(job.events), job.last_sequence) == original
    second, _, _ = new_job(event_cache_bytes=1)
    assert not second.events and list(job.events) == original[1]


@async_test
async def test_frame_limit_requires_resync_without_damaging_the_live_projection():
    job, publisher, _ = new_job(event_cache_bytes=10000, max_frame_bytes=4096)
    delta(publisher, text='x' * 20000)
    assert job.view()['incomplete'] is False
    row = job.view()['messages'][0]
    assert row['content'].startswith('xxx') and row['content_truncated']
    assert encoded_size(job.view()) <= job.settings.max_frame_bytes
    assert job.event_bytes == sum(encoded_size(item) for item in job.events)
    with pytest.raises(WebError, match='resync_required'):
        job.check_cursor(job.last_sequence - 1)
    job.check_cursor(job.last_sequence)


@async_test
async def test_frame_gap_watermark_cannot_move_back_when_older_cached_events_are_evicted():
    from naiyue_test_agent.agent_runtime.message_observation import _snapshot
    job, publisher, _ = new_job(event_capacity=2, event_cache_bytes=10000, max_frame_bytes=4096)
    delta(publisher, text='short')
    publisher.publish('message_completed', _snapshot(AIMessage(id='message', content='x' * 20000), 'graph_values'))
    omitted = job.last_sequence
    publisher.publish('run_finished', RunFinishedData('completed'))
    job.done = True
    assert job._evicted_through == omitted
    assert job.view()['messages'][0]['provisional'] is False
    assert job.view()['incomplete'] is False
    with pytest.raises(WebError, match='resync_required'):
        job.check_cursor(omitted - 1)
    job.check_cursor(job.last_sequence)
    assert [frame async for frame in job.stream(omitted)] == [
        {'event': 'runtime', 'id': str(job.last_sequence), 'data': job.events[-1]}]


@async_test
async def test_oversized_child_completion_updates_its_snapshot_without_crossing_into_root():
    from naiyue_test_agent.agent_runtime.events import AgentEventSource
    from naiyue_test_agent.agent_runtime.message_observation import _snapshot
    job, publisher, events = new_job(max_frame_bytes=4096)
    delta(publisher, text='root text')
    event = replace(events[-1], sequence=job.last_sequence + 1, event_type='message_completed',
        data=_snapshot(AIMessage(id='child-message', content='child text' * 2000), 'graph_values'),
        source=AgentEventSource('root/tester/child', 'child', 'child-run', 'run', 'delegate'))
    job.observe(event)
    row = job.agent_view('root/tester/child')['messages'][0]
    assert row['provisional'] is False and row['state'] == 'complete' and row['content_truncated']
    assert job.view()['messages'][0]['content'] == 'root text'
    assert job.view()['incomplete'] is False
    assert all(encoded_size(item) <= job.settings.max_frame_bytes for item in job.events)
    with pytest.raises(WebError, match='resync_required'):
        job.check_cursor(event.sequence - 1)
    job.check_cursor(event.sequence)


@async_test
async def test_open_stream_reports_gap_after_later_byte_eviction():
    job, publisher, _ = new_job(event_cache_bytes=600)
    stream = job.stream(0)
    first = await anext(stream)
    assert first['data']['event_type'] == 'run_started'
    delta(publisher, text='x' * 2000)
    gap = await anext(stream)
    assert gap == {'event': 'resync_required', 'data': {'code': 'resync_required'}}
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert job.view()['messages'][0]['content'] == 'x' * 2000
    assert not job.view()['incomplete']


@async_test
async def test_progress_and_stop_snapshot_survive_complete_replay_eviction():
    from test_web_tool_progress import setup_job, start, progress, finish
    from naiyue_test_agent.contracts.session_events import ControlData
    job, publisher = setup_job(event_cache_bytes=1)
    start(publisher)
    progress(publisher, text='最新进度', completed=12)
    assert not job.events and job.event_bytes == 0
    assert job.view()['tool_states']['call']['text'] == '最新进度'
    assert job.view()['tool_states']['call']['state'] == 'running'
    publisher.publish('control_accepted', ControlData('stop', 'stop'))
    assert job.view()['stop_requested']
    finish(publisher)
    assert job.view()['tool_states']['call']['state'] == 'finished'
    assert job.view()['incomplete'] is False


@async_test
async def test_child_and_root_share_replay_budget_but_keep_separate_live_messages():
    from naiyue_test_agent.agent_runtime.events import AgentEventSource
    job, publisher, events = new_job(event_cache_bytes=600)
    delta(publisher, text='父消息')
    child = replace(events[-1], sequence=job.last_sequence + 1,
        data=MessageDeltaData('child-message', '子消息' * 500, 1),
        source=AgentEventSource('root/tester/child', 'child', 'child-run', 'run', 'delegate'))
    job.observe(child)
    assert not job.events and job.event_bytes == 0
    assert job.view()['messages'][0]['content'] == '父消息'
    assert job.agent_view('root/tester/child')['messages'][0]['content'] == '子消息' * 500
    assert job.view()['incomplete'] is False
    with pytest.raises(WebError, match='resync_required'):
        job.check_cursor(child.sequence - 1)
    job.check_cursor(child.sequence)


@async_test
async def test_http_byte_gap_refreshes_complete_history_without_reexecuting(monkeypatch, tmp_path):
    from test_async_application import StreamingToolModel
    from test_web_api import web_client, request
    model = StreamingToolModel(replies=[AIMessage(content='完整答复')])
    async with web_client(monkeypatch, tmp_path, model, event_cache_bytes=1) as (client, app):
        sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
        body = request('问题')
        receipt = (await client.post(f'/api/sessions/{sid}/runs', json=body)).json()
        rid = receipt['run_id']
        await asyncio.wait_for(app.state.delivery.wait(rid), 5)
        gap = await client.get(f'/api/runs/{rid}/events?after=0')
        assert gap.status_code == 409 and gap.json()['error']['code'] == 'resync_required'
        view = (await client.get(f'/api/runs/{rid}')).json()
        assert view['status'] == 'completed' and view['incomplete'] is False
        assert [row['content'] for row in view['messages']] == ['问题', '完整答复']
        assert (await client.get(f'/api/runs/{rid}/events?after={view["last_sequence"]}')).status_code == 200
        history = (await client.get(f'/api/sessions/{sid}/messages')).json()
        assert '完整答复' in str(history)
        assert (await client.post(f'/api/sessions/{sid}/runs', json=body)).json() == receipt
        assert len(model.seen) == 1
        assert not app.state.delivery.get(rid).events
