"""Execute accepted memory work without retaining the completed root run."""

import asyncio
from naiyue_test_agent.contracts.run_context import RunIdentity
from naiyue_test_agent.execution_roots import ExecutionRoot
from datetime import datetime, timezone
import json
import logging
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent.agent_runtime.async_utils import run_blocking, await_settled
from naiyue_test_agent.agent_runtime.run_control import UserStop, native_async_model
from naiyue_test_agent.agent_runtime.stream_events import MEMORY_TAG
from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.model_access import MODEL_LIMITS_METADATA_KEY, MODEL_ID_METADATA_KEY
from naiyue_test_agent.model_access.memory_output import prepare_memory_output
from .memory_batches import build_batch, model_batch
from naiyue_test_agent.contracts.memory import MemoryReadError, safe_memory_reason
from naiyue_test_agent.diagnostics import safe_log
from .memory_sources import sensitive
from .memory_wiki_protocol import validate_overview
from naiyue_test_agent.application.observability.run_events import RunObservation


logger = logging.getLogger(__name__)

def _check(job):
    if job.control.requested:
        raise UserStop()
    if time.monotonic() >= job.deadline:
        raise TimeoutError()


def _limits(model, settings):
    metadata = getattr(model, 'metadata', None) or {}
    limits = metadata.get(MODEL_LIMITS_METADATA_KEY, {}) if isinstance(metadata, dict) else None
    if not isinstance(limits, dict):
        raise MemoryReadError('memory_model_budget_invalid')
    for key in ('context_window_tokens', 'max_output_tokens'):
        if key in limits and (type(limits[key]) is not int or limits[key] <= 0):
            raise MemoryReadError('memory_model_budget_invalid')
    output = min(settings.max_output_tokens, limits.get('max_output_tokens', settings.max_output_tokens))
    cap = min(settings.max_input_tokens, limits['context_window_tokens'] - output) if 'context_window_tokens' in limits else settings.max_input_tokens
    if cap <= 0:
        raise MemoryReadError('memory_model_budget_invalid')
    if 'context_window_tokens' not in limits:
        logger.warning('memory_model_budget_unverified')
    return cap, output


def _bounded_notes(notes):
    if not isinstance(notes, list):
        return ['coverage_notes_truncated:unknown'] if notes else []
    kept, omitted, unknown = [], 0, False
    for note in notes:
        if isinstance(note, str) and note.startswith('coverage_notes_truncated:'):
            count = note.removeprefix('coverage_notes_truncated:')
            if count.isdecimal():omitted += int(count)
            else:unknown = True
        elif isinstance(note, str):
            if note not in kept:kept.append(note)
        else:unknown = True
    while True:
        marker = [f'coverage_notes_truncated:{"unknown" if unknown else omitted}'] if omitted or unknown else []
        result = kept + marker
        if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) <= 4096:
            return result
        kept.pop(0)
        omitted += 1


def _messages(system, previous, batch, coverage=None):
    visible_coverage = {key: coverage[key] for key in ('lower', 'upper', 'skipped', 'has_newer_messages')
        if key in coverage} if coverage is not None else None
    payload = dict(previous_overview=previous, previous_coverage=visible_coverage,
        allowed_scopes=['workspace','personal'],batch=model_batch(batch))
    return [system, HumanMessage(content=json.dumps(payload, ensure_ascii=False))]


async def _prepare(service, job, upper):
    cap, output = _limits(job.model, service.settings)
    from .memory_wiki_protocol import WikiExtractionResult,EXTRACT_PROMPT as WIKI_PROMPT
    schema=WikiExtractionResult
    prompt=WIKI_PROMPT
    bound, output_mode, overhead = prepare_memory_output(job.model, schema.model_json_schema(), output)
    cap -= overhead
    system = SystemMessage(content=prompt.format(overview_chars=service.settings.overview_chars,
        max_proposals=service.settings.max_proposals)
        +'\nOutput JSON schema:\n'+json.dumps(schema.model_json_schema(),ensure_ascii=False))
    saved = await run_blocking(service.storage.memory.overview, job.target_session_id)
    previous = saved['overview'] if saved['overview_status'] == 'generated' else None
    coverage = dict(saved['coverage'], skipped=_bounded_notes(saved['coverage']['skipped'])) if previous is not None else None
    try:
        sensitive([previous, coverage])
    except MemoryReadError as error:
        logger.warning('memory_previous_overview_unavailable error_type=%s', safe_error_type(error))
        previous = None
        coverage = None
    budget = cap - count_tokens_approximately(_messages(system, previous, {}, coverage))
    if budget <= 0:
        raise MemoryReadError('memory_batch_budget')
    batch = await run_blocking(build_batch, service.storage.memory, session_id=job.target_session_id,
        thread_id=job.thread_id, conversation_revision=job.conversation_revision,
        start=job.start, upper=upper, token_budget=budget, source_chars=service.settings.read_chunk_chars, max_messages=service.settings.batch_max_messages, max_read_chars=service.settings.batch_read_chars,
        overlap_chars=service.settings.batch_overlap_chars, context_floor=max(1,job.requested_start or 1))
    messages = _messages(system, previous, batch, coverage)
    if coverage is not None and coverage['history_version'] != batch['history_version']:
        raise MemoryReadError('memory_overview_source_changed')
    if count_tokens_approximately(messages) > cap:
        raise MemoryReadError('memory_batch_budget')
    return batch, messages, bound, coverage, output_mode


async def _commit(job, counter, call, *args, **kwargs):
    # Shield the actual operation so cancellation cannot hide a confirmed successful save.
    pending = asyncio.create_task(run_blocking(call, *args, **kwargs))
    try:
        return await await_settled(pending)
    finally:
        if pending.done() and not pending.cancelled() and pending.exception() is None:
            result=pending.result()
            increment=result if counter=='saved' and type(result) is int else 1
            setattr(job, counter, getattr(job, counter) + increment)


def _failure(job, diagnostic, error, *, stage='job', proposal_index=0):
    job.failed += 1
    job.error_type = safe_error_type(error)
    reason = safe_memory_reason(error) if isinstance(error, MemoryReadError) else None
    detail = getattr(error, '_diagnostic_reason', None) if reason == 'memory_proposal_invalid' else None
    safe_log(logger.error, '%s task_id=%s batch=%s stage=%s proposal_index=%s error_type=%s reason=%s detail=%s',
        diagnostic, job.task_id, job.batches, stage, proposal_index, job.error_type, reason, detail)


async def _batch(service, job):
    async with service._slots:
        _check(job)
        job.status = 'running'
        job.started_at = job.started_at or datetime.now(timezone.utc).isoformat()
        stage = 'prepare'
        try:
            lower, upper = job.start, job.upper
            batch, messages, bound, previous_coverage, output_mode = await _prepare(service, job, upper)
            _check(job)
            job.batches += 1
            job.skipped.extend(batch['skipped'])
            if not any(u['origin']=='new' and u['text'] for u in batch['units']):
                job.start = tuple(batch['next_position'])
                return
            config = dict(tags=[MEMORY_TAG], metadata={MODEL_ID_METADATA_KEY: job.model_id,
                'memory_task_id': job.task_id, 'memory_batch': job.batches,'memory_stage':'extract'})
            stage = 'model'
            if native_async_model(job.model):
                response = await job.control.await_model(lambda: bound.ainvoke(messages, config=config))
            else:
                response = await run_blocking(bound.invoke, messages, config=config)
            _check(job)
            stage = 'model_output'
            if output_mode != 'text':
                response = response['raw']
            from .memory_wiki_protocol import parse,WikiExtractionResult
            parsed=parse(response,output_mode,WikiExtractionResult)
            if (not isinstance(parsed,dict) or set(parsed)!={'overview','proposals'}
                    or not isinstance(parsed['proposals'],list) or len(parsed['proposals'])>service.settings.max_proposals):
                raise MemoryReadError('memory_result_invalid')
            next_sequence, next_offset = batch['next_position']
            consumed_upper = next_sequence if next_offset else next_sequence - 1
            coverage_lower = max(1, lower[0])
            skipped, previous_end = [], None
            current_start, current_end = (coverage_lower, lower[1]), (next_sequence, next_offset)
            if previous_coverage is not None:
                coverage_lower = min(coverage_lower, previous_coverage['lower'])
                previous_end = (previous_coverage['upper'] + 1, 0)
                partial = []
                for note in previous_coverage['skipped']:
                    fields = note.split(':') if isinstance(note, str) else []
                    if len(fields) == 3 and fields[0] == 'partial_source' and all(p.isdecimal() for p in fields[1:]):
                        point = (int(fields[1]), int(fields[2]))
                        if point[0] == previous_coverage['upper']:
                            partial.append(point)
                        elif not current_start <= point < current_end:
                            skipped.append(note.replace('partial_source:', 'inherited_partial_coverage:', 1))
                    else:
                        skipped.append(note)
                if partial:previous_end = max(partial)
                if current_start > previous_end:
                    skipped.append(f'unprocessed_until:{previous_end[0]}:{previous_end[1]}:{current_start[0]}:{current_start[1]}'
                        if current_start[1] else f'unprocessed:{previous_end[0]}:{previous_end[1]}:{current_start[0] - 1}')
                if partial and current_end <= previous_end:
                    skipped.append(f'partial_source:{previous_end[0]}:{previous_end[1]}')
            elif current_start[1]:
                skipped.append(f'unprocessed_until:{current_start[0]}:0:{current_start[0]}:{current_start[1]}')
            skipped.extend(job.skipped)
            if next_offset and (previous_end is None or current_end > previous_end):
                skipped.append(f'partial_source:{next_sequence}:{next_offset}')
            skipped = _bounded_notes(skipped)
            stage = 'overview_save'
            try:
                parsed['overview']=validate_overview(parsed['overview'],service.settings)
                await _commit(job, 'overview_saved', service.storage.memory.save_overview, session_id=job.target_session_id, thread_id=job.thread_id,
                    conversation_revision=job.conversation_revision, overview=parsed['overview'],
                    lower=coverage_lower, upper=consumed_upper, task_id=job.task_id,
                    origin_run_id=job.origin_run_id, batch=job.batches, skipped=skipped,
                    expected_history_version=batch['history_version'])
            except Exception as error:
                _failure(job, 'memory_overview_save_failed', error, stage=stage)
            stage = 'comparison'
            from .memory_organization import organize
            await organize(service,job,batch,parsed['proposals'])
            job.start = tuple(batch['next_position'])
        except (Exception, asyncio.CancelledError) as error:
            if isinstance(error,(UserStop,asyncio.CancelledError)) or time.monotonic()>=job.deadline:
                raise
            if 'batch' not in locals():raise
            _failure(job,'memory_batch_failed',error,stage=stage)
            job.skipped.append(f'failed_batch:{lower[0]}:{lower[1]}:{batch["next_position"][0]}:{batch["next_position"][1]}')
            job.start=tuple(batch['next_position'])
        finally:
            if job.status == 'running':job.status = 'starting'


async def _wait_change(job):
    job.changed.clear()
    _check(job)
    await job.control.await_model(job.changed.wait)


async def drive(service, job):
    observation = RunObservation(service._publisher(job.session_id, job.task_id), mode='memory',
        origin_run_id=job.origin_run_id, stop_control=job.control)
    result, termination = 'completed', 'completed'
    observation.start()
    try:
        with observation.bind(identity=RunIdentity(job.session_id, job.task_id, project_id=job.project_id, execution_root=ExecutionRoot(job.root) if job.root is not None else None)):
            async with asyncio.timeout(max(0, job.deadline - time.monotonic())):
                while True:
                    _check(job)
                    if not job.released:
                        await _wait_change(job)
                        continue
                    if job.wiki_request and job.wiki_request['action']=='organize':
                        from .memory_organization import organize
                        async with service._slots:
                            job.batches=1
                            job.status='running'
                            job.started_at=job.started_at or datetime.now(timezone.utc).isoformat()
                            await organize(service,job,None,[])
                        break
                    if job.start[0] > job.upper:
                        if job.pending_runs:
                            if job.batches >= service.settings.max_batches:
                                termination = 'batch_limit'
                                break
                            await _wait_change(job)
                            continue
                        break
                    if job.batches >= service.settings.max_batches:
                        termination = 'batch_limit'
                        break
                    observation.stage = 'execute'
                    await job.control.await_model(lambda: _batch(service, job))
                if job.failed or termination != 'completed':
                    result = 'partial' if job.saved or job.overview_saved else 'failed'
                    observation.failed(MemoryReadError('memory_task_incomplete'))
                else:
                    observation.result({}, None)
    except UserStop:
        termination = job.termination or ('application_close' if job.control.reason == 'close' else
            'root_not_completed' if job.control.reason == 'failure' else 'user_stop')
        if job.control.reason == 'failure' and termination != 'root_not_completed':
            error = MemoryReadError('memory_admission_failed')
            _failure(job, 'memory_job_failed', error)
            result = 'partial' if job.saved or job.overview_saved else 'failed'
            observation.failed(error)
        else:
            result = 'stopped'
            observation.result({'run_stopped': True}, None)
    except (Exception, asyncio.CancelledError) as error:
        _failure(job, 'memory_job_failed', error)
        result = 'partial' if job.saved or job.overview_saved else 'failed'
        termination = 'timeout' if isinstance(error, TimeoutError) else 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed'
        observation.cancelled(error) if isinstance(error, asyncio.CancelledError) else observation.failed(error)
    finally:
        if termination not in ('completed', 'user_stop', 'application_close', 'root_not_completed'):
            job.skipped.append(f'unprocessed:{job.start[0]}:{job.start[1]}:{job.upper}')
        service._terminal(job, result, termination)
        safe_log(logger.info, 'memory_job_finished task_id=%s result_status=%s termination=%s batches=%s '
            'overview_saved=%s memory_saved=%s failed=%s skipped_count=%s', job.task_id, result,
            termination, job.batches, job.overview_saved, job.saved, job.failed, len(job.skipped))
        observation.finish(False)
