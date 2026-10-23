"""Transient Web progress survives replay gaps without becoming tool results."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path

from naiyue_test_agent.agent_runtime.events import RunEventPublisher, RunStartedData
from naiyue_test_agent.agent_runtime.stream_events import ToolProgressData
from naiyue_test_agent.contracts.session_events import ControlData
from naiyue_test_agent.web.delivered_run import DeliveredRun
from naiyue_test_agent.web.settings import WebSettings
from naiyue_test_agent.tools.settings import ToolProgressSettings
from naiyue_test_agent.settings import AppSettings


def setup_job(**options):
    settings = WebSettings(database_path=Path('unused'), frontend_dist=Path('unused'),
                           event_capacity=2, **options)
    rows = [{'id': 'batch', 'type': 'ai', 'content': '', 'provisional': False,
             'state': 'complete', 'tool_calls': [{'id': 'call', 'name': 'bash', 'args': {}}]}]
    progress_settings = settings.runtime_settings.progress if settings.runtime_settings else None
    job = DeliveredRun('session', rows, settings, lambda _: None, progress_settings=progress_settings)
    job.observed = []
    publisher = RunEventPublisher('session', 'run', (job.observe, job.observed.append))
    publisher.publish('run_started', RunStartedData('message'))
    return job, publisher


def start(publisher):
    from naiyue_test_agent.agent_runtime.events import ToolCallStartedData
    publisher.publish('tool_call_started', ToolCallStartedData('call', 'bash', 'dispatch'))


def progress(publisher, **overrides):
    values = dict(batch_id='batch', tool_call_id='call', tool_name='bash',
                  activity='running', phase='process_output', completed=8, unit='bytes', text='first')
    values.update(overrides)
    publisher.publish('tool_progress', ToolProgressData(**values))


def finish(publisher):
    from naiyue_test_agent.agent_runtime.events import ToolCallFinishedData
    publisher.publish('tool_call_finished', ToolCallFinishedData('call', 'bash', 'dispatch', 'success'))


def test_snapshot_retains_latest_progress_when_ring_overflows_and_replay_requires_resync():
    async def scenario():
        job, publisher = setup_job()
        start(publisher)
        progress(publisher)
        progress(publisher, completed=12, text='<script>latest</script>')
        row = job.view()['tool_states']['call']
        assert row['text'] == '<script>latest</script>' and row['completed'] == 12
        assert row['batch_id'] == 'batch' and row['state'] == 'running'
        assert row['started_at'] and row['last_activity_at']
        assert len(job.events) == 2 and len(job.tool_states) == 1
        frames = [item async for item in job.stream(0)]
        assert frames == [{'event': 'resync_required', 'data': {'code': 'resync_required'}}]
        assert all('latest' not in row['content'] for row in job.view()['messages'])
    asyncio.run(scenario())


def test_finished_and_foreign_or_old_progress_cannot_revert_current_snapshot():
    async def scenario():
        job, publisher = setup_job()
        start(publisher)
        progress(publisher)
        old = job.events[-1]
        finish(publisher)
        expected = job.view()['tool_states']['call']
        assert expected['state'] == 'finished' and expected['text'] == 'first'
        progress(publisher, text='LATE')
        assert job.view()['tool_states']['call'] == expected
        from naiyue_test_agent.agent_runtime.events import RuntimeEvent
        job.observe(RuntimeEvent('tool_progress', 'session', 'foreign', 999, '',
                    ToolProgressData('batch', 'call', 'bash', 'running', 'late')))
        assert job.last_sequence == old['sequence'] + 2 and not job.view()['incomplete']
        # Reused ID belongs to a new AI batch. Older progress cannot bind to it.
        job.projection.seed('session', [{'id': 'next', 'type': 'ai', 'content': '',
            'tool_calls': [{'id': 'call', 'name': 'bash', 'args': {}}]}])
        start(publisher)
        progress(publisher, batch_id='batch', text='OLD_BATCH')
        assert job.view()['tool_states']['call']['batch_id'] == 'next'
        assert 'text' not in job.view()['tool_states']['call']
        progress(publisher, batch_id='next', text='NEW_BATCH')
        publisher.publish('control_accepted', ControlData('stop', 'stop'))
        latest = job.observed[-1]
        assert job.view()['stop_requested'] is True
        assert job.view()['tool_states']['call']['state'] == 'running'
        # Duplicate/out-of-order observation is a no-op, not a gap.
        job.observe(replace(latest, sequence=1))
        assert not job.view()['incomplete']
    asyncio.run(scenario())


def test_progress_text_is_utf8_bounded_before_sse_and_snapshot_without_mutating_domain_event():
    async def scenario():
        settings = AppSettings(progress=ToolProgressSettings(max_text_bytes=7))
        job, publisher = setup_job(runtime_settings=settings)
        start(publisher)
        progress(publisher, text='prefix你好世界')
        event = job.observed[-1]
        assert job.events[-1]['data']['text'] == '世界'
        assert job.view()['tool_states']['call']['text'] == '世界'
        assert event.data.text == 'prefix你好世界'
        json.dumps(job.view(), allow_nan=False)
    asyncio.run(scenario())


def test_run_terminal_marks_only_unfinished_tools_unconfirmed_and_blocks_late_progress():
    async def scenario():
        from naiyue_test_agent.agent_runtime.events import RunFinishedData
        job, publisher = setup_job()
        start(publisher)
        progress(publisher)
        publisher.publish('run_finished', RunFinishedData('stopped', stop_mode='active'))
        expected = job.view()['tool_states']['call']
        assert expected['state'] == 'unconfirmed'
        progress(publisher, text='TOO_LATE')
        assert job.view()['tool_states']['call'] == expected
    asyncio.run(scenario())


def test_http_receives_progress_before_tool_returns_and_restores_latest_after_replay_gap(monkeypatch, tmp_path):
    from langchain.tools import tool, ToolRuntime
    from langchain_core.messages import AIMessage
    from pydantic import BaseModel
    from naiyue_test_agent.tools import ToolSpec
    from naiyue_test_agent.tools.permissions import ToolPermission, require_tool_permit
    from naiyue_test_agent.tools.progress import ToolProgress
    from naiyue_test_agent.tools.schemas import build_tool_schemas
    from test_async_application import StreamingToolModel, call
    from test_web_api import web_client, request

    async def scenario():
        entered, release, received = asyncio.Event(), asyncio.Event(), asyncio.Event()
        @tool
        async def probe(runtime: ToolRuntime) -> str:
            """Report progress then await deterministic test release."""
            require_tool_permit(runtime)
            ToolProgress(runtime, ToolProgressSettings(max_text_bytes=9)).report(
                phase='working', completed=3, unit='entries', text='TRANSIENT_PROGRESS')
            entered.set()
            await release.wait()
            return 'FINAL_RESULT'
        public, execution = build_tool_schemas('WebProgressInput', BaseModel)
        probe.args_schema = execution
        spec = ToolSpec(probe, public, permissions=ToolPermission(default='allow'))
        model = StreamingToolModel(replies=[call('probe', {}, 'call'), AIMessage(content='done')])
        # Exercise startup loader wiring too; no actual user config is changed.
        from importlib import import_module
        web_module = import_module('naiyue_test_agent.web.app')
        monkeypatch.setattr(web_module, 'load_settings', lambda: AppSettings(
            progress=ToolProgressSettings(max_text_bytes=9)))
        async with web_client(monkeypatch, tmp_path, model, event_capacity=2) as (client, app):
            backend = app.state.service.application
            await backend.tools.register(spec)
            sid = (await client.post('/api/sessions', json={'model_id': 'A'})).json()['session_id']
            await backend.sessions.select_tools(sid, ['probe'])
            def observe(event):
                if event.event_type == 'tool_progress' and event.data.phase == 'working':
                    received.set()
            unsubscribe = backend.observability.subscribe(observe)
            run = (await client.post(f'/api/sessions/{sid}/runs', json=request('run'))).json()['run_id']
            try:
                assert app.state.delivery.get(run).progress_max_text_bytes == 9
                await asyncio.wait_for(entered.wait(), 5)
                await asyncio.wait_for(received.wait(), 5)
                view = (await client.get(f'/api/runs/{run}')).json()
                assert view['status'] == 'running' and view['done'] is False
                assert view['tool_states']['call']['text'] == '_PROGRESS'
                assert view['tool_states']['call']['completed'] == 3
                assert all(row['type'] != 'tool' for row in view['messages'])
                assert (await client.get(f'/api/runs/{run}/events')).status_code == 409
                refreshed = (await client.get(f'/api/sessions/{sid}')).json()
                assert refreshed['live']['tool_states'] == view['tool_states']
            finally:
                release.set()
                await app.state.delivery.wait(run)
                unsubscribe()
            final = (await client.get(f'/api/runs/{run}')).json()
            assert final['tool_states']['call']['state'] == 'finished'
            assert final['tool_states']['call']['text'] == '_PROGRESS'
            assert next(row for row in final['messages'] if row['type'] == 'tool')['content'] == 'FINAL_RESULT'
            assert 'TRANSIENT_PROGRESS' not in str(model.seen)
    asyncio.run(scenario())


def test_failed_producer_without_run_terminal_does_not_leave_tools_running():
    from types import SimpleNamespace
    from naiyue_test_agent.web.run_delivery import RunDelivery
    async def scenario():
        job, publisher = setup_job()
        start(publisher)
        progress(publisher)
        async def fail(*args):
            raise OSError('synthetic producer fault')
        delivery = RunDelivery(SimpleNamespace(runs=SimpleNamespace(start=fail)), job.settings)
        job.unsubscribe = lambda: None
        await delivery._produce(job, SimpleNamespace(text='run', request_id='request'),
                                receipt_key=(job.session_id, 'request'))
        assert job.done and job.status == 'unconfirmed'
        row = job.view()['tool_states']['call']
        assert row['state'] == 'unconfirmed' and row['text'] == 'first'
        assert row['finished_at']
    asyncio.run(scenario())
