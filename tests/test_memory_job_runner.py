"""The memory worker owns bounded model calls and drains its actual commits."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from naiyue_test_agent.agent_runtime.events import RunEventPublisher
from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback
from naiyue_test_agent.agent_runtime.run_control import RunControl
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.application.memory import memory_overview_store as store
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.model_access import ModelCatalog, MODEL_LIMITS_METADATA_KEY
from test_async_application import StreamingToolModel
from test_memory_batches import archive, save, result, proposed
from test_memory_lifecycle import proposal


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), 5)


def fixture(archive, tmp_path, model, **changes):
    settings = MemorySettings(**(dict(auto_enabled=False, related_limit=0) | changes))
    memory = MemoryAccess(archive, settings, tmp_path / 'data')
    catalog = ModelCatalog('A', {'A': model})
    install_model_usage_callback(catalog)
    events = []
    service = SimpleNamespace(memory=memory, storage=archive, settings=settings, catalog=catalog,
        _slots=asyncio.Semaphore(1), allowed=lambda request: True,
        _publisher=lambda sid, rid: RunEventPublisher(sid, rid, (events.append,)))
    def terminal(job, status, termination):
        job.status, job.result_status, job.termination = 'finished', status, termination
        job.cleanup_confirmed = True
    service._terminal = terminal
    job = SimpleNamespace(task_id='a' * 32, session_id='session', target_session_id='session',
        root=tmp_path, origin_run_id='root-run', model_id='A', model=model, thread_id='root-thread',
        conversation_revision=0, project_id=archive.sessions.get('session')['project_id'], deadline=time.monotonic() + 30, start=(0, 0), upper=1,
        batches=0, saved=0, failed=0, overview_saved=0, skipped=[], control=RunControl(),
        released=True, pending_runs=set(), changed=asyncio.Event(), status='starting',
        started_at=None, result_status=None, termination=None, cleanup_confirmed=False, error_type=None)
    return service, job, events


def response(proposals=None):
    return AIMessage(content=json.dumps(result(proposals=proposals or []), ensure_ascii=False),
        response_metadata={'finish_reason': 'stop'})


def test_normal_worker_saves_overview_and_memory_with_independent_event_identity(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='以后使用.venv', additional_kwargs={'naiyue_human_input': True})])
        model = StreamingToolModel(replies=[response([proposed()])])
        service, job, events = fixture(archive, tmp_path, model)
        try:
            await drive(service, job)
            assert job.result_status == 'completed' and job.cleanup_confirmed
            assert job.saved == 1 and job.overview_saved == 1 and job.batches == 1 and job.failed == 0
            assert archive.memory.overview('session')['overview'] == result()['overview']
            assert archive.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0] == 1
            starts = [e for e in events if e.event_type == 'run_started']
            ends = [e for e in events if e.event_type == 'run_finished']
            calls = [e for e in events if e.event_type == 'model_call_started']
            assert len(starts) == len(ends) == len(calls) == 1
            assert starts[0].run_id == job.task_id and starts[0].data.origin_run_id == 'root-run'
            assert calls[0].data.purpose == 'memory' and calls[0].data.model_id == 'A'
            assert ends[0].data.status == 'completed' and all(e.run_id != 'root-run' for e in events)
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_invalid_proposal_and_permission_failure_preserve_independent_success(archive, tmp_path, caplog):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='以后使用.venv', additional_kwargs={'naiyue_human_input': True})])
        model = StreamingToolModel(replies=[response([{'unexpected': 'SYNTHETIC_PRIVATE'}, proposed()])])
        service, job, events = fixture(archive, tmp_path, model)
        service.allowed = lambda request: request['scope'] != 'workspace'
        try:
            await drive(service, job)
            assert job.result_status == 'partial' and job.overview_saved == 1 and job.saved == 0 and job.failed == 2
            assert [e for e in events if e.event_type == 'run_finished'][0].data.status == 'failed'
            assert 'SYNTHETIC_PRIVATE' not in caplog.text and 'memory_proposal_failed' in caplog.text
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_json_failure_has_no_false_save_and_no_model_repair_loop(archive, tmp_path, caplog):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='visible')])
        model = StreamingToolModel(replies=[AIMessage(content='{SYNTHETIC_PRIVATE')])
        service, job, events = fixture(archive, tmp_path, model)
        try:
            await drive(service, job)
            assert job.result_status == 'failed' and job.failed == 1 and job.cleanup_confirmed
            assert job.overview_saved == job.saved == 0 and len(model.seen) == 1
            assert archive.memory.overview('session')['overview'] is None
            assert 'SYNTHETIC_PRIVATE' not in caplog.text
            assert [e for e in events if e.event_type == 'run_finished'][0].data.status == 'failed'
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_waiting_root_stop_never_takes_slot_or_calls_model(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        model = StreamingToolModel(replies=[])
        service, job, events = fixture(archive, tmp_path, model)
        job.released = False
        worker = asyncio.create_task(drive(service, job))
        try:
            await until(lambda: bool(events))
            assert not service._slots.locked() and not model.seen and not worker.done()
            job.control.request(); job.changed.set()
            await asyncio.wait_for(worker, 5)
            assert job.result_status == 'stopped' and job.cleanup_confirmed
            assert [e for e in events if e.event_type == 'run_finished'][0].data.status == 'stopped'
        finally:
            service.memory.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('termination,status', [('root_not_completed', 'stopped'),
    ('source_changed', 'failed'), ('admission_save_failed', 'failed')])
def test_admission_failure_is_distinguished_from_an_uncompleted_root(archive, tmp_path, termination, status):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        model = StreamingToolModel(replies=[])
        service, job, events = fixture(archive, tmp_path, model)
        job.released = False
        job.termination = termination
        job.control.request('failure')
        try:
            await drive(service, job)
            assert job.result_status == status and job.termination == termination and not model.seen
            assert [e for e in events if e.event_type == 'run_finished'][0].data.status == status
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_native_model_stop_and_expired_deadline_do_not_save(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='visible')])
        model = StreamingToolModel(replies=[response()], entered=asyncio.Event(), release=asyncio.Event())
        service, job, events = fixture(archive, tmp_path, model)
        worker = asyncio.create_task(drive(service, job))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            job.control.request(); job.changed.set()
            await asyncio.wait_for(worker, 5)
            assert job.result_status == 'stopped' and job.overview_saved == 0 and not service._slots.locked()
            service2, job2, events2 = fixture(archive, tmp_path, StreamingToolModel(replies=[]))
            try:
                job2.deadline = time.monotonic() - 1
                await drive(service2, job2)
                assert job2.result_status == 'failed' and job2.termination == 'timeout' and job2.cleanup_confirmed
                assert service2.memory.storage is archive and not service2._slots.locked()
            finally:
                service2.memory.close()
        finally:
            model.release.set()
            service.memory.close()
    asyncio.run(scenario())


def test_merging_root_wait_does_not_hold_slot_and_processes_new_saved_upper(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='one', content='first source')])
        model = StreamingToolModel(replies=[response(), response()])
        service, job, events = fixture(archive, tmp_path, model)
        job.pending_runs.add(('session', 'second-root'))
        worker = asyncio.create_task(drive(service, job))
        try:
            await until(lambda: job.overview_saved == 1 and job.start[0] > job.upper)
            assert not worker.done() and not service._slots.locked()
            save(archive, [HumanMessage(id='two', content='second source')])
            job.upper = 2; job.pending_runs.clear(); job.changed.set()
            await asyncio.wait_for(worker, 5)
            assert job.result_status == 'completed' and job.batches == 2 and job.overview_saved == 2
            assert len(model.seen) == 2
            assert archive.memory.overview('session')['coverage']['upper'] == 2
        finally:
            service.memory.close()
    asyncio.run(scenario())


class CapturingModel(StreamingToolModel):
    kwargs_seen: list = Field(default_factory=list)
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.kwargs_seen.append(kwargs)
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_long_message_respects_model_window_output_cap_and_batch_limit(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='long', content='长🧪文本' * 10000)])
        model = CapturingModel(replies=[response(), response(), response()], metadata={
            MODEL_LIMITS_METADATA_KEY: {'context_window_tokens': 2300, 'max_output_tokens': 300}})
        service, job, events = fixture(archive, tmp_path, model, max_input_tokens=3000, max_output_tokens=1000)
        try:
            await drive(service, job)
            assert job.result_status == 'partial' and job.termination == 'batch_limit' and job.batches == 3
            assert job.overview_saved == 3 and len(model.seen) == 3
            assert all(count_tokens_approximately(messages) <= 2000 for messages in model.seen)
            assert all(call['max_tokens'] == 300 for call in model.kwargs_seen)
            assert all('temperature' not in call and 'thinking' not in call for call in model.kwargs_seen)
            assert job.start[0] == 1 and job.start[1] > 0 and job.skipped
        finally:
            service.memory.close()
    asyncio.run(scenario())


class ThreadModel(BaseChatModel):
    entered: object
    release: object
    @property
    def _llm_type(self):
        return 'threaded-memory-test'
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError('Test thread was not released')
        return ChatResult(generations=[ChatGeneration(message=response())])


def test_sync_model_stop_waits_for_thread_before_terminal_cleanup(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='visible')])
        model = ThreadModel(entered=threading.Event(), release=threading.Event())
        service, job, events = fixture(archive, tmp_path, model)
        worker = asyncio.create_task(drive(service, job))
        try:
            assert await asyncio.to_thread(model.entered.wait, 5)
            job.control.request(); job.changed.set()
            for _ in range(10):
                await asyncio.sleep(0)
            assert not worker.done() and not job.cleanup_confirmed and service._slots.locked()
            model.release.set()
            await asyncio.wait_for(worker, 5)
            assert job.cleanup_confirmed and job.result_status == 'stopped' and job.overview_saved == 0
            assert not service._slots.locked()
        finally:
            model.release.set()
            await worker
            service.memory.close()
    asyncio.run(scenario())


def test_queued_stop_does_not_wait_for_an_unrelated_task_or_lose_a_slot(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='visible')])
        model = StreamingToolModel(replies=[])
        service, job, events = fixture(archive, tmp_path, model)
        await service._slots.acquire()
        worker = asyncio.create_task(drive(service, job))
        try:
            await until(lambda: bool(service._slots._waiters))
            job.control.request(); job.changed.set()
            await asyncio.wait_for(worker, 5)
            assert job.result_status == 'stopped' and not model.seen and service._slots.locked()
        finally:
            service._slots.release()
            service.memory.close()
        assert service._slots._value == 1
    asyncio.run(scenario())


def test_stop_during_necessary_commit_drains_and_counts_the_real_save(archive, tmp_path, monkeypatch):
    from naiyue_test_agent.application.memory import memory_job_runner
    async def scenario():
        save(archive, [HumanMessage(id='human', content='visible')])
        entered, release = threading.Event(), threading.Event()
        original = archive.memory.save_overview
        def gated(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test commit was not released')
            return original(*args, **kwargs)
        monkeypatch.setattr(archive.memory, 'save_overview', gated)
        service, job, events = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]))
        worker = asyncio.create_task(memory_job_runner.drive(service, job))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            job.control.request(); job.changed.set()
            for _ in range(10):
                await asyncio.sleep(0)
            assert not worker.done() and not job.cleanup_confirmed
            release.set()
            await asyncio.wait_for(worker, 5)
            assert job.result_status == 'stopped' and job.overview_saved == 1 and job.cleanup_confirmed
            assert archive.memory.overview('session')['overview'] == result()['overview']
        finally:
            release.set()
            await worker
            service.memory.close()
    asyncio.run(scenario())


def test_source_rewritten_during_model_call_cannot_save_stale_overview_or_memory(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv', additional_kwargs={'naiyue_human_input': True})])
        model = StreamingToolModel(replies=[response([proposed()])], entered=asyncio.Event(), release=asyncio.Event())
        service, job, events = fixture(archive, tmp_path, model)
        worker = asyncio.create_task(drive(service, job))
        try:
            await asyncio.wait_for(model.entered.wait(), 5)
            save(archive, [HumanMessage(id='human', content='更正了原始来源')])
            model.release.set()
            await asyncio.wait_for(worker, 5)
            assert job.overview_saved == job.saved == 0 and job.failed == 2 and job.result_status == 'failed'
            assert archive.memory.overview('session')['overview'] is None
        finally:
            model.release.set()
            await worker
            service.memory.close()
    asyncio.run(scenario())


def test_related_active_and_candidate_records_enter_the_same_single_model_call(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from naiyue_test_agent.tools.memory import MemoryInput
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv', additional_kwargs={'naiyue_human_input': True})],
            occurred_at=time.time())
        model = StreamingToolModel(replies=[response()])
        service, job, events = fixture(archive, tmp_path, model, related_limit=2, preview_chars=10)
        ref = dict(session_id='session', thread_id='root-thread', message_id='human', revision=1,
            conversation_revision=0, quote='使用.venv', offset=0)
        for intent in ('activate', 'candidate'):
            value = dict(proposal(reason='用户明确约定'), subject='environment ' + intent, intent=intent,
                body='使用.venv ' + 'existing detail ' * 40,
                gaps=['等待确认测试环境'] if intent == 'candidate' else [])
            request = MemoryInput(action='record', scope='workspace', proposal=value, sources=[ref]).model_dump(mode='json')
            service.memory.query('session', service.storage.sessions.get('session')['project_id'], request)
        try:
            await drive(service, job)
            assert job.result_status == 'completed' and len(model.seen) == 1
            payload = json.loads(model.seen[0][-1].content)
            assert {r['status'] for r in payload['related_memories']} == {'active', 'candidate'}
            assert all('sources' not in row and row['memory_id'] for row in payload['related_memories'])
            candidate = next(r for r in payload['related_memories'] if r['status'] == 'candidate')
            assert candidate['complete'] and len(candidate['body']) > service.settings.preview_chars
            assert candidate['gaps'] == ['等待确认测试环境'] and candidate['reason'] == '用户明确约定'
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_candidate_changed_after_search_is_skipped_with_diagnostic(archive, tmp_path, monkeypatch, caplog):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from naiyue_test_agent.tools.memory import MemoryInput
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv', additional_kwargs={'naiyue_human_input': True})],
            occurred_at=time.time())
        model = StreamingToolModel(replies=[response()])
        service, job, events = fixture(archive, tmp_path, model, related_limit=2)
        ref = dict(session_id='session', thread_id='root-thread', message_id='human', revision=1,
            conversation_revision=0, quote='使用.venv', offset=0)
        value = dict(proposal(reason='用户明确约定'), intent='candidate', gaps=['等待确认'])
        record = MemoryInput(action='record', scope='workspace', proposal=value, sources=[ref]).model_dump(mode='json')
        saved = service.memory.query('session', service.storage.sessions.get('session')['project_id'], record)['item']
        original = service.memory.query
        reads = []
        def changed(sid, root, request):
            if request['action'] == 'read' and not reads:
                reads.append(request)
                update = MemoryInput(action='update', scope='workspace', memory_id=saved['memory_id'],
                    expected_version=1, proposal=dict(value, gaps=['另一个会话已更新']), sources=[ref]).model_dump(mode='json')
                original(sid, root, update)
            return original(sid, root, request)
        monkeypatch.setattr(service.memory, 'query', changed)
        try:
            await drive(service, job)
            assert job.result_status == 'completed' and job.overview_saved == 1
            assert reads[0]['version'] == 1
            assert json.loads(model.seen[0][-1].content)['related_memories'] == []
            assert 'memory_related_read_failed' in caplog.text and 'version_changed' in caplog.text
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_optional_related_budget_pressure_does_not_discard_valid_primary_sources(archive, tmp_path, monkeypatch):
    from naiyue_test_agent.application.memory import memory_job_runner
    from naiyue_test_agent.application.memory.memory_queries import MemoryReadError
    async def scenario():
        save(archive, [HumanMessage(id='human', content='visible')])
        service, job, events = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]))
        original, calls = archive.memory.build_batch, []
        def constrained(*args, **kwargs):
            calls.append(kwargs['token_budget'])
            if len(calls) == 2:
                raise MemoryReadError('memory_batch_budget')
            return original(*args, **kwargs)
        async def related(*args):
            return [dict(memory_id='b' * 32, body='bounded optional context')]
        monkeypatch.setattr(archive.memory, 'build_batch', constrained)
        monkeypatch.setattr(memory_job_runner, '_related', related)
        try:
            await memory_job_runner.drive(service, job)
            assert job.result_status == 'completed' and job.overview_saved == 1 and job.failed == 0
            payload = json.loads(job.model.seen[0][-1].content)
            assert payload['batch']['sources'][0]['text'] == 'visible' and payload['related_memories'] == []
        finally:
            service.memory.close()
    asyncio.run(scenario())
