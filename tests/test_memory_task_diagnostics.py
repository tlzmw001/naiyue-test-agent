"""Memory diagnostics explain failures without exposing saved or model text."""

import asyncio
import logging
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from naiyue_test_agent.application.memory.memory_batches import proposal_request
from naiyue_test_agent.application.memory.memory_job_runner import drive
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_async_application import StreamingToolModel
from test_memory_batches import archive, save
from test_memory_flat_contract import flat, source, old
from test_memory_job_runner import fixture, response


@pytest.mark.parametrize('item,sources,related,reason', [
    (flat(body=123), [source()], [], 'fields_invalid'),
    (flat(memory_id='a' * 32), [source()], [], 'update_target_unavailable'),
    (flat(memory_id='a' * 32, kind='rule'), [source()], [old()], 'update_scope_or_kind'),
    (flat(), [source(), source()], [], 'source_key_ambiguous'),
    (flat(sources=[dict(source_key='missing', quote='PRIVATE_SOURCE')]), [source()], [], 'source_key_missing'),
    (flat(sources=[dict(source_key='1:0', quote='PRIVATE_SOURCE')]), [source()], [], 'quote_not_found'),
    (flat(), [source(text='使用.venv 使用.venv')], [], 'quote_ambiguous'),
])
def test_proposal_diagnostic_keeps_public_error_contract(item, sources, related, reason):
    with pytest.raises(MemoryReadError) as caught:
        proposal_request(item, sources, related)
    assert type(caught.value) is MemoryReadError
    assert str(caught.value) == 'memory_proposal_invalid'
    assert caught.value._diagnostic_reason == reason


@pytest.mark.parametrize('mode', ['partial', 'completed', 'batch_limit', 'model_output', 'permission', 'stopped', 'timeout', 'overview'])
def test_worker_logs_correlated_safe_failure_and_terminal_counts(archive, tmp_path, caplog, monkeypatch, mode):
    caplog.set_level(logging.INFO, logger='naiyue_test_agent.application.memory.memory_job_runner')
    async def scenario():
        text = '使用.venv' if mode != 'batch_limit' else '使用.venv ' * 100
        save(archive, [HumanMessage(id='human', content=text, additional_kwargs={'naiyue_human_input': True})])
        replies = [response([])]
        changes = {}
        if mode == 'partial':
            replies = [response([flat(sources=[dict(source_key='1:0', quote='PRIVATE_MODEL_TEXT')])])]
        elif mode == 'model_output':
            replies = [AIMessage(content='{PRIVATE_MODEL_TEXT')]
        elif mode in ('permission', 'overview'):
            replies = [response([flat()])]
        elif mode == 'batch_limit':
            changes = dict(max_input_tokens=1100, read_chunk_chars=20, max_batches=1)
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=replies), **changes)
        if mode == 'permission':service.allowed = lambda request: False
        if mode == 'stopped':job.control.request()
        if mode == 'timeout':job.deadline = 0
        if mode == 'overview':
            def reject_overview(**kwargs):raise MemoryReadError('memory_source_changed')
            monkeypatch.setattr(service.storage.memory, 'save_overview', reject_overview)
        try:
            await drive(service, job)
            records = [r.getMessage() for r in caplog.records if r.name.endswith('memory_job_runner')]
            summaries = [r for r in records if r.startswith('memory_job_finished ')]
            assert len(summaries) == 1
            summary = summaries[0]
            for field, value in dict(task_id=job.task_id, result_status=job.result_status,
                    termination=job.termination, batches=job.batches, overview_saved=job.overview_saved,
                    memory_saved=job.saved, failed=job.failed, skipped_count=len(job.skipped)).items():
                assert f'{field}={value}' in summary
            assert 'PRIVATE_MODEL_TEXT' not in caplog.text and text not in caplog.text
            if mode in ('partial', 'permission'):
                assert (job.result_status, job.overview_saved, job.saved, job.failed) == ('partial', 1, 0, 1)
                failure = next(r for r in records if r.startswith('memory_proposal_failed '))
                assert f'task_id={job.task_id}' in failure and 'batch=1' in failure and 'proposal_index=1' in failure
                assert 'stage=proposal_validation' in failure
                assert ('detail=quote_not_found' if mode == 'partial' else 'reason=memory_permission_denied') in failure
            elif mode == 'completed':
                assert job.result_status == 'completed' and job.saved == 0 and job.failed == 0
            elif mode == 'batch_limit':
                assert job.result_status == 'partial' and job.termination == 'batch_limit'
            elif mode == 'stopped':
                assert job.result_status == 'stopped' and job.termination == 'user_stop'
                assert job.saved == job.overview_saved == job.failed == 0
            elif mode == 'timeout':
                assert job.result_status == 'failed' and job.termination == 'timeout'
                assert job.saved == job.overview_saved == 0 and job.failed == 1
            elif mode == 'overview':
                assert (job.result_status, job.overview_saved, job.saved, job.failed) == ('partial', 0, 1, 1)
                failure = next(r for r in records if r.startswith('memory_overview_save_failed '))
                assert 'stage=overview_save' in failure and 'reason=memory_source_changed' in failure
            else:
                assert job.result_status == 'failed' and job.saved == job.overview_saved == 0
                failure = next(r for r in records if r.startswith('memory_batch_failed '))
                assert 'stage=model_output' in failure and 'reason=memory_result_invalid' in failure
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_service_entry_keeps_memory_summary_without_enabling_provider_info():
    script = '''
import logging
from naiyue_test_agent.service import __main__ as entry
def load(_):
    logging.getLogger('naiyue_test_agent.application.memory.memory_job_runner').info('memory_job_finished task_id=fixture')
    logging.getLogger('httpx').info('PRIVATE_PROVIDER_INFO')
    raise RuntimeError('PRIVATE_STARTUP_ERROR')
entry.load_settings = load
assert entry.main(['--config', 'unused', '--settings', 'unused']) == 1
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert 'memory_job_finished task_id=fixture' in result.stderr
    assert 'PRIVATE_PROVIDER_INFO' not in result.stderr
    assert 'PRIVATE_STARTUP_ERROR' not in result.stderr


def test_log_sink_failure_does_not_change_partial_result(archive, tmp_path, monkeypatch):
    from naiyue_test_agent.application.memory import memory_job_runner as runner
    calls = []
    def broken(*args, **kwargs):
        calls.append(args[0])
        raise OSError('PRIVATE_LOG_SINK')
    monkeypatch.setattr(runner.logger, 'error', broken)
    monkeypatch.setattr(runner.logger, 'info', broken)
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv')])
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[response([{'invalid': True}])]))
        try:
            await drive(service, job)
            assert (job.result_status, job.overview_saved, job.saved, job.failed) == ('partial', 1, 0, 1)
            assert job.cleanup_confirmed and len(calls) == 2
        finally:
            service.memory.close()
    asyncio.run(scenario())
