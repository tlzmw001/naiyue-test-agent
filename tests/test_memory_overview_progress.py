"""Cumulative overview provenance survives batches, gaps and queue waits."""

import asyncio
import json
import time

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.application.memory import memory_overview_store as store
from test_async_application import StreamingToolModel
from test_memory_batches import archive, save
from test_memory_job_runner import fixture, response, until


def test_many_batches_and_two_tasks_inherit_used_overview_range_and_omissions(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='secret', content='password = SYNTHETIC_NOT_A_CREDENTIAL'),
            HumanMessage(id='long', content='visible source ' * 20)])
        first_model = StreamingToolModel(replies=[response() for _ in range(10)])
        first_service, first, _ = fixture(archive, tmp_path, first_model,
            max_input_tokens=1100, read_chunk_chars=20, max_batches=10)
        first.upper = 2
        try:
            await drive(first_service, first)
            assert first.result_status == 'completed' and first.batches > 1
            saved = archive.memory.overview('session')
            assert saved['coverage']['lower'] == 1 and saved['coverage']['upper'] == 2
            assert saved['coverage']['skipped'] == ['sensitive:1']
            assert not any(note.startswith('partial_source:') for note in saved['coverage']['skipped'])
            save(archive, [HumanMessage(id='later', content='new saved work')])
            second_model = StreamingToolModel(replies=[response()])
            second_service, second, _ = fixture(archive, tmp_path, second_model)
            second.task_id = 'b' * 32; second.start = (3, 0); second.upper = 3
            try:
                await drive(second_service, second)
                assert second.result_status == 'completed'
                current = archive.memory.overview('session')['coverage']
                assert current['lower'] == 1 and current['upper'] == 3
                assert current['skipped'] == ['sensitive:1']
                payload = json.loads(second_model.seen[0][-1].content)
                assert payload['previous_coverage']['lower'] == 1
                assert payload['previous_coverage']['skipped'] == ['sensitive:1']
            finally:
                second_service.memory.close()
        finally:
            first_service.memory.close()
    asyncio.run(scenario())


def test_failed_accepted_range_is_a_hole_in_the_next_successful_overview(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='secret', content='password = SYNTHETIC_NOT_A_CREDENTIAL'),
            *[HumanMessage(id=f'm{i}', content=f'work {i}') for i in range(2, 7)]])
        service1, first, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]))
        first.upper = 2
        await drive(service1, first)
        failed_service, failed, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[AIMessage(content='{')]))
        failed.task_id = 'b' * 32; failed.start = (3, 0); failed.upper = 4
        archive.memory.accept('session', thread_id='root-thread', conversation_revision=0, upper=4)
        await drive(failed_service, failed)
        assert failed.result_status == 'failed'
        service3, third, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]))
        third.task_id = 'c' * 32; third.start = (5, 0); third.upper = 6
        try:
            await drive(service3, third)
            assert third.result_status == 'completed'
            coverage = archive.memory.overview('session')['coverage']
            assert coverage['lower'] == 1 and coverage['upper'] == 6
            assert coverage['skipped'] == ['sensitive:1', 'unprocessed:3:0:4']
        finally:
            for service in (service1, failed_service, service3):
                service.memory.close()
    asyncio.run(scenario())


def test_abandoned_partial_source_becomes_real_gap_instead_of_stale_partial(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='long', content='visible source ' * 100)])
        service1, first, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]),
            max_input_tokens=1100, read_chunk_chars=20, max_batches=1)
        await drive(service1, first)
        assert first.result_status == 'partial' and first.start[0] == 1 and first.start[1] > 0
        offset = first.start[1]
        save(archive, [HumanMessage(id='later', content='after skipped tail')])
        service2, second, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]))
        second.task_id = 'b' * 32; second.start = (2, 0); second.upper = 2
        try:
            await drive(service2, second)
            coverage = archive.memory.overview('session')['coverage']
            assert coverage['lower'] == 1 and coverage['upper'] == 2
            assert coverage['skipped'] == [f'unprocessed:1:{offset}:1']
            assert not any(note.startswith('partial_source:') for note in coverage['skipped'])
        finally:
            service1.memory.close(); service2.memory.close()
    asyncio.run(scenario())


def test_many_omission_notes_remain_bounded_with_explicit_truncation(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [*[HumanMessage(id=f's{i}', content='password = SYNTHETIC_NOT_A_CREDENTIAL')
            for i in range(700)], HumanMessage(id='safe', content='safe final source')])
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]))
        job.upper = 701
        try:
            await drive(service, job)
            assert job.result_status == 'completed' and job.overview_saved == 1
            notes = archive.memory.overview('session')['coverage']['skipped']
            assert len(json.dumps(notes, ensure_ascii=False, separators=(',', ':')).encode()) <= 4096
            assert any(note.startswith('coverage_notes_truncated:') for note in notes)
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_only_slot_owner_is_running_and_pending_root_wait_is_starting(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='visible')])
        model = StreamingToolModel(replies=[response()], entered=asyncio.Event(), release=asyncio.Event())
        service, job, _ = fixture(archive, tmp_path, model)
        job.pending_runs.add(('session', 'next-root'))
        await service._slots.acquire()
        worker = asyncio.create_task(drive(service, job))
        released = False
        try:
            await until(lambda: bool(service._slots._waiters))
            assert job.status == 'starting' and job.started_at is None
            service._slots.release(); released = True
            await asyncio.wait_for(model.entered.wait(), 5)
            assert job.status == 'running' and job.started_at is not None
            model.release.set()
            await until(lambda: job.overview_saved == 1 and job.start[0] > job.upper)
            assert job.status == 'starting' and not service._slots.locked()
            job.pending_runs.clear(); job.changed.set()
            await asyncio.wait_for(worker, 5)
            assert job.result_status == 'completed'
        finally:
            if not released:
                service._slots.release()
            model.release.set(); job.pending_runs.clear(); job.changed.set()
            await worker
            service.memory.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('denied', ['search', 'read'])
def test_background_related_reads_honor_each_normalized_request_permission(archive, tmp_path, monkeypatch, denied):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from naiyue_test_agent.tools.memory import MemoryInput
    from test_memory_lifecycle import proposal
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv', additional_kwargs={'naiyue_human_input': True})],
            occurred_at=time.time())
        model = StreamingToolModel(replies=[response()])
        service, job, _ = fixture(archive, tmp_path, model, related_limit=2)
        request = MemoryInput(action='record', scope='workspace', proposal=dict(proposal(),
            body='使用.venv PRIVATE_CANDIDATE_BODY', intent='candidate', gaps=['需确认']),
            sources=[dict(session_id='session', thread_id='root-thread', message_id='human', revision=1,
                conversation_revision=0, quote='使用.venv', offset=0)]).model_dump(mode='json')
        service.memory.query('session', service.storage.sessions.get('session')['project_id'], request)
        original, calls = service.memory.query, []
        def observed(*args):
            calls.append(args[-1])
            return original(*args)
        monkeypatch.setattr(service.memory, 'query', observed)
        service.allowed = lambda request: request['action'] != denied
        try:
            await drive(service, job)
            assert job.result_status == 'completed' and job.overview_saved == 1
            assert all(request['action'] != denied for request in calls)
            assert 'PRIVATE_CANDIDATE_BODY' not in model.seen[0][-1].content
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_rewrite_after_previous_overview_read_cannot_revalidate_old_summary(archive, tmp_path, monkeypatch):
    from naiyue_test_agent.application.memory import memory_job_runner
    async def scenario():
        save(archive, [HumanMessage(id='old', content='old saved work')])
        first_service, first, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[response()]))
        await memory_job_runner.drive(first_service, first)
        save(archive, [HumanMessage(id='new', content='new work')])
        model = StreamingToolModel(replies=[response()])
        second_service, second, _ = fixture(archive, tmp_path, model)
        second.task_id = 'b' * 32; second.start = (2, 0); second.upper = 2
        original, calls = archive.memory.build_batch, []
        def rewritten(*args, **kwargs):
            if not calls:
                calls.append(True)
                save(archive, [HumanMessage(id='old', content='corrected saved work')])
            return original(*args, **kwargs)
        monkeypatch.setattr(archive.memory, 'build_batch', rewritten)
        try:
            await memory_job_runner.drive(second_service, second)
            assert second.result_status == 'failed' and second.overview_saved == 0 and not model.seen
            overview = archive.memory.overview('session')
            assert overview['overview_version'] == 1 and overview['overview_status'] == 'stale'
        finally:
            first_service.memory.close(); second_service.memory.close()
    asyncio.run(scenario())
