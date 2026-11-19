"""Bounded real-model memory acceptance on frozen synthetic cases and temporary stores.

This is an acceptance probe, not product seed data. Semantic judgments remain
human review items; deterministic checks do not prove natural-language support.
"""

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

from scripts.memory_evaluation_cases import evaluation_model_identity, safe_report, seed_sources


FIXTURES = Path(__file__).resolve().parents[1] / 'docs/research/memory00/fixtures.json'
MAX_LOGICAL_CALLS = 24


def load_cases(path=FIXTURES):
    raw = Path(path).read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    protocol = json.loads(FIXTURES.with_name('protocol.json').read_text())
    if checksum != protocol['fixtures_sha256']:
        raise ValueError('semantic_fixture_hash_changed')
    value = json.loads(raw)
    cases = value['extraction_cases']
    if not value['synthetic'] or len(cases) != 12:
        raise ValueError('semantic_fixture_invalid')
    for case in cases:
        ids = {s['id'] for s in case['sources']}
        if len(ids) != len(case['sources']) or not set(case['expected']['required_sources']) <= ids:
            raise ValueError('semantic_source_identity_invalid')
    return cases, checksum


def seed_candidate(memory, sid, cases, *, at):
    """Fill e11's missing old-state precondition, without editing its labels."""
    from naiyue_test_agent.tools.memory import MemoryInput
    text = cases[4]['sources'][0]['text']
    source = dict(id='probe-e11-prior-assistant', role='assistant', text=text)
    seeded = seed_sources(memory.storage, sid, [source], at=at)
    parent = memory.storage.sessions.get(sid)
    reference = dict(session_id=sid, thread_id=parent['thread_id'], message_id=source['id'],
        revision=1, conversation_revision=parent['conversation_revision'], quote=text, offset=0)
    proposal = dict(kind='context', title='自动化用例编排', body=text, subject='自动化用例编排',
        basis='inference', intent='candidate', gaps=['缺持久选择'], reason='旧助手提出编排，尚缺用户持久选择')
    first = MemoryInput(action='record', scope='workspace', proposal=proposal, sources=[reference])
    saved = memory.query(sid, memory.storage.sessions.get(sid)['project_id'], first.model_dump(mode='json'), now=at + 1)['item']
    update = MemoryInput(action='update', scope='workspace', memory_id=saved['memory_id'], expected_version=1,
        proposal=proposal, sources=[reference])
    saved = memory.query(sid, memory.storage.sessions.get(sid)['project_id'], update.model_dump(mode='json'), now=at + 2)['item']
    return dict(kind='supplemental_synthetic_precondition', fixture_alias='candidate-e11',
        actual_memory_id=saved['memory_id'], version=saved['version'], scope='workspace',
        body_from='e05.sources[0].text', source=source, source_texts=seeded['source_texts'],
        explanation='原e11未给旧候选正文；此补充只还原同ID补证需要的旧状态，不代表原样本覆盖已验证。')


def evaluation_protocol_identity(args):
    if args.suite=='legacy':path=FIXTURES.with_name('protocol.json')
    else:
        root=Path(__file__).resolve().parents[1]/'tests/data'/('memory_space_time' if args.suite=='space-time' else 'memory_optimization')
        path=Path(args.protocol or root/'protocol.json')
    rubric=path.with_name('rubric.md')
    return dict(protocol_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        rubric_sha256=hashlib.sha256(rubric.read_bytes()).hexdigest() if rubric.exists() else None)


def check_case(case, rows, *, baseline, source_texts=None, candidate_id=None):
    known = source_texts or {s['id']: s['text'] for s in case['sources']}
    changed = [r for r in rows if r['version'] > baseline.get(r['memory_id'], 0)]
    expected, failures = case['expected'], []
    for row in changed:
        if not row['sources'] or any(s['message_id'] not in known or not s.get('quote')
                or s['quote'] not in known[s['message_id']] for s in row['sources']):
            failures.append('source_reference_not_supported')
    if expected['status'] == 'active':
        matched = [r for r in changed if r['status'] == 'active' and r['kind'] == expected['kind']]
        if not matched:
            failures.append('expected_active_kind_missing')
        observed = {s['message_id'] for row in matched for s in row['sources']}
        if not set(expected['required_sources']) <= observed:
            failures.append('required_sources_missing')
        if case.get('candidate') and not any(r['memory_id'] == candidate_id
                and r['version'] > case['candidate']['version'] for r in matched):
            failures.append('candidate_identity_or_version_not_preserved')
    elif expected['status'] == 'not_active' and any(r['status'] == 'active' for r in changed):
        failures.append('negative_case_became_active')
    elif expected['status'] == 'discard' and changed:
        failures.append('discard_case_created_memory')
    return dict(structural_status='fail' if failures else 'pass', failures=failures,
        semantic_status='needs_human_review', assertion=expected['assertion'],
        forbidden_claim=expected.get('forbidden_claim'),
        forbidden_checks=[dict(assertion=item, status='needs_human_review') for item in expected['forbidden']],
        note='结构检查只核类型、状态、来源和版本；不自动证明范围、因果、措辞及禁止断言的语义。')


def probe_callback(budget, settings, *, output_mode='text'):
    from langchain_core.callbacks import AsyncCallbackHandler
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.model_access import MODEL_ID_METADATA_KEY
    from naiyue_test_agent.application.memory.memory_proposals import parse_stage
    from naiyue_test_agent.contracts.memory import MemoryReadError

    class ProbeCallback(AsyncCallbackHandler):
        raise_error = True
        run_inline = True

        def __init__(self):
            self.budget, self.outputs, self.inputs = budget, [], []
            from scripts.memory_evaluation_costs import CaseCosts
            self.costs=CaseCosts()
            self.stages = {}

        async def on_chat_model_start(self, serialized, messages, *, run_id=None, metadata=None, **kwargs):
            from langchain_core.messages import message_to_dict
            self.budget.reserve((metadata or {}).get(MODEL_ID_METADATA_KEY))
            self.stages[run_id]=(metadata or {}).get('memory_stage','extract')
            self.costs.start(run_id,self.stages[run_id])
            self.inputs.append([safe_report(message_to_dict(message)) for group in messages for message in group])

        async def on_llm_error(self,error,*,run_id=None,**kwargs):
            self.costs.finish(run_id,failed=True)

        async def on_llm_end(self, response, *, run_id=None, **kwargs):
            groups = response.generations
            message = groups[0][0].message if len(groups) == 1 and groups[0] else None
            self.costs.finish(run_id,message.usage_metadata if isinstance(message,AIMessage) else None)
            stage = self.stages.pop(run_id, 'extract')
            try:
                if not isinstance(message, AIMessage):
                    raise ValueError('semantic_response_shape_invalid')
                if stage == 'followup':
                    self.outputs.append(dict(stage=stage, answer=safe_report(message.content)))
                    return
                self.outputs.append(dict(stage=stage,parsed=safe_report(parse_stage(message, settings, stage=stage,output_mode=output_mode))))
            except Exception as error:
                self.outputs.append(dict(stage=stage,
                    parse_error_type=type(error).__name__,
                    error_code=str(error) if isinstance(error, MemoryReadError) else 'response_parse_failed',
                    raw_text=safe_report(str(message.text)) if isinstance(message, AIMessage) else None))

    return ProbeCallback()


def saved_rows(memory):
    result = []
    for _, owner in memory._stores('both'):
        with owner.database.transaction() as connection:
            for raw in connection.execute('SELECT * FROM naiyue_memories ORDER BY memory_id LIMIT 50'):
                row = dict(raw)
                row['sources'] = [dict(s) for s in connection.execute('''SELECT * FROM naiyue_memory_sources
                    WHERE memory_id=? AND memory_version=? ORDER BY source_id''', (row['memory_id'], row['version']))]
                result.append(row)
    return result


async def run_case(case, repeat, catalog, model_id, callback, settings):
    from naiyue_test_agent.agent_runtime.events import RunEventPublisher
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
    from naiyue_test_agent.storage.memory_sql.memory_overview_store import overview
    from naiyue_test_agent.storage.project_store import ProjectStore

    events, started = [], time.monotonic()
    from scripts.memory_evaluation_costs import CaseCosts
    callback.costs=CaseCosts()
    callback.outputs = []
    callback.inputs = []
    with tempfile.TemporaryDirectory(prefix='nyt-memory-semantic-') as temporary:
        root = Path(temporary).resolve()
        with ProjectStore(root / 'sessions.sqlite') as storage:
            storage.sessions.bind_workspace(str(uuid4()), root)
            storage.sessions.create('probe', dict(thread_id='probe', model_id=model_id, workspace_root=root,
                active_tool_names=['memory'], permission_mode='Default'))
            memory = MemoryAccess(storage, settings, root / 'data')
            coordinator = MemoryJobs(memory, catalog, publisher_factory=lambda sid, rid:
                RunEventPublisher(sid, rid, (events.append,)))
            setup, texts, candidate_id = [], {}, None
            restore_costs=None
            at = time.time() - 3600
            try:
                if case.get('candidate'):
                    candidate = seed_candidate(memory, 'probe', load_cases()[0], at=at)
                    setup.append(candidate)
                    texts.update(candidate['source_texts'])
                    candidate_id = candidate['actual_memory_id']
                if case.get('space_time'):
                    from scripts.memory_space_time_probe import seed_old
                    old_setup=seed_old(memory, 'probe', case, at)
                    setup.extend(old_setup)
                    texts.update({s['source']['id']:s['source']['text'] for s in old_setup})
                prior_upper = storage.database.read_rows('SELECT COALESCE(MAX(sequence),0) AS n FROM naiyue_chat_messages WHERE session_id=?', ('probe',))[0]['n']
                baseline = {r['memory_id']: r['version'] for r in saved_rows(memory)}
                coordinator.prepare('probe', root, [], catalog.get(model_id))
                sources = [dict(v, role='user') if case.get('space_time') and v['role']=='human' else v for v in case['sources']]
                seeded = seed_sources(storage, 'probe', sources, at=at + 10)
                texts.update(seeded['source_texts'])
                setup.extend(seeded['setup'])
                restore_costs=callback.costs.observe(memory)
                origin = uuid4().hex
                accepted = await coordinator.request(source_session_id='probe', target_session_id='probe',
                    workspace_root=root, origin_run_id=origin, model_id=model_id, tool_call_id='probe-refresh',
                    **({'start_sequence':prior_upper+1} if case.get('space_time') else {}))
                await coordinator.after_finish('probe', origin, 'completed', model_id=model_id)
                await coordinator.wait_idle()
                task = await coordinator.output(accepted['task_id'], session_id='probe', workspace_root=root)
                rows = saved_rows(memory)
                answer = None
                if case.get('space_time'):
                    from scripts.memory_space_time_probe import followup
                    answer = await followup(memory, case, catalog.get(model_id), model_id)
                from naiyue_test_agent.application.observability.diagnostic_persistence import project_diagnostic_event
                from naiyue_test_agent.settings import DiagnosticSettings
                diagnostics = DiagnosticSettings()
                for event in events:
                    if event.event_type.startswith('model_call_'):
                        storage.usage.save_model_usage_event(event)
                    fact = project_diagnostic_event(event)
                    if fact is not None:
                        storage.diagnostics.save_diagnostic_event(fact, diagnostics)
                    if event.event_type == 'run_finished':
                        storage.usage.finish_model_usage_run(event.session_id, event.run_id)
                        storage.diagnostics.finish_diagnostic_run(fact, diagnostics)
                starts = [e.data.invocation_id for e in events if e.event_type == 'model_call_started']
                finishes = [e.data.invocation_id for e in events if e.event_type == 'model_call_finished']
                return dict(costs=callback.costs.snapshot(),followup=answer, answer_requirements=case.get('answer_requirements'), expected_facts=case.get('expected_facts'),case_id=case['id'], repeat=repeat, source_time=at + 10, setup=setup,
                    source_texts=texts, task=task, overview=storage.memory.overview('probe'), memories=rows,
                    model_outputs=callback.outputs, model_inputs=callback.inputs, invocation_pairing=starts == finishes,
                    usage=storage.usage.read_model_usage('probe', model_limit=20),
                    events=[dict(event_type=e.event_type, run_id=e.run_id, data=asdict(e.data)) for e in events
                        if e.event_type in ('run_started', 'run_finished', 'model_call_started', 'model_call_finished',
                            'model_http_attempt_started', 'model_http_attempt_finished')],
                    checks=check_case(case, rows, baseline=baseline, source_texts=texts, candidate_id=candidate_id),
                    duration_ms=round((time.monotonic() - started) * 1000))
            finally:
                try:
                    await coordinator.aclose()
                finally:
                    if restore_costs is not None:restore_costs()
                    memory.close()


def selected_cases(args, *, calls_per_case=None):
    from scripts.memory_evaluation_cases import load_suite,planned_calls
    if getattr(args, 'suite', 'legacy') == 'space-time':
        root = Path(__file__).resolve().parents[1] / 'tests/data/memory_space_time'
        cases, protocol = load_suite(args.fixtures or root/'fixtures.json', args.protocol or root/'protocol.json')
        selected = [dict(c, id=c['case_id'], space_time=True,
            expected=dict(status='fact_review', assertion='逐事实审阅空间、时间和后续回答', forbidden=c['forbidden']))
            for c in cases if c['split'] == args.split]
        planned_calls(len(selected), args.repeat, 3)
        return selected, protocol['suite_fixtures_sha256']
    if getattr(args,'suite','legacy')=='legacy':
        cases,checksum=load_cases()
        planned_calls(len(cases),args.repeat,calls_per_case or 2)
        return cases,checksum
    cases,protocol=load_suite(getattr(args,'fixtures',None),getattr(args,'protocol',None))
    selected=[c for c in cases if c['suite']=='semantic' and c['split']==args.split]
    planned_calls(len(selected),args.repeat,calls_per_case or 2)
    return [dict(id=c['case_id'],sources=c['sources'],expected_facts=c['expected_facts'],expected=dict(kind=c['kind'] if c['must_activate'] else None,
        status='active' if c['must_activate'] else 'fact_review',
        forbidden_claim=c['claim'] if c['must_not_activate'] else None,
        required_sources=[v['id'] for v in c['sources']] if c['must_activate'] else [],
        assertion=c['assertion'],forbidden=c['forbidden'])) for c in selected],protocol['suite_fixtures_sha256']


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--model-id')
    parser.add_argument('--repeat', type=int, choices=(1, 2), default=1)
    parser.add_argument('--suite',choices=('legacy','optimization','space-time'),default='legacy')
    parser.add_argument('--split',choices=('calibration','validation','holdout'),default='calibration')
    parser.add_argument('--fixtures',type=Path)
    parser.add_argument('--protocol',type=Path)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error('--output must not already exist')
    if args.model_id is not None and not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', args.model_id):
        parser.error('invalid model identifier')
    return args


async def run(args):
    from naiyue_test_agent.agent_runtime.model_usage_observation import install_model_usage_callback
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.model_access import load_model_catalog
    from scripts.provider_acceptance_budget import RequestBudget

    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    # Provider/library logging may contain URLs or exception bodies. Finite
    # failures and owned callback events are retained in the report instead.
    logging.disable(logging.CRITICAL)
    cases, checksum = selected_cases(args)
    from scripts.evaluate_memory_optimization import evidence_manifest
    report = dict(run_id=uuid4().hex, manifest=evidence_manifest(), suite=args.suite, split=args.split,
        synthetic=True, fixtures_sha256=checksum, max_logical_calls=MAX_LOGICAL_CALLS,
        repeats=args.repeat, semantic_status='needs_human_review', cases=[], failures=[])
    report.update(evaluation_protocol_identity(args))
    if args.suite == 'space-time':
        report['report_schema_version'] = 2
        report['entry'] = 'background'
        report['review_contract'] = 'memory-facts-v1'
        report['policy'] = 'candidate' if 'convention' in str(__import__('naiyue_test_agent.tools.memory', fromlist=['MemoryInput']).MemoryInput.model_fields['kind'].annotation) else 'baseline'
    catalog, budget = None, RequestBudget(MAX_LOGICAL_CALLS)
    try:
        catalog = load_model_catalog(args.config)
        model_id = args.model_id or catalog.default_model_id
        if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', model_id):
            raise ValueError('semantic_model_identifier_invalid')
        selected = catalog.get(model_id)
        report['model_id'] = model_id
        settings = MemorySettings(auto_enabled=False,max_batches=1)
        report['effective_model']=evaluation_model_identity(selected,settings)
        observer = probe_callback(budget, settings, output_mode=(selected.profile or {}).get('memory_output', 'text'))
        from langchain_core.callbacks.base import BaseCallbackManager
        seen = set()
        for identity in catalog.model_ids:
            model = catalog.get(identity)
            if id(model) in seen:
                continue
            seen.add(id(model))
            previous = model.callbacks
            handlers = previous.handlers if isinstance(previous, BaseCallbackManager) else previous or []
            model.callbacks = [observer, *handlers]
        catalog.prepare_async_clients()
        install_model_usage_callback(catalog)
        for repeat in range(1, args.repeat + 1):
            for case in cases:
                if budget.snapshot()['used'] >= MAX_LOGICAL_CALLS:
                    break
                try:
                    value = await run_case(case, repeat, catalog, model_id, observer, settings)
                    report['cases'].append(value)
                except Exception as error:
                    report['failures'].append(dict(case_id=case['id'], repeat=repeat, error_type=type(error).__name__,costs=observer.costs.snapshot()))
    except Exception as error:
        report['failures'].append(dict(stage='prepare', error_type=type(error).__name__))
    finally:
        if catalog is not None:
            try:
                await catalog.aclose()
            except Exception as error:
                report['failures'].append(dict(stage='close', error_type=type(error).__name__))
    report['logical_budget'] = budget.snapshot()
    report['expected_cases'] = len(cases) * args.repeat
    report['executed_cases'] = len(report['cases'])
    report['structural_failures'] = sum(value['checks']['structural_status'] != 'pass'
        or value['task']['result_status'] != 'completed' or not value['invocation_pairing']
        or (value.get('followup') or {}).get('status') == 'failed' for value in report['cases'])
    complete = not report['failures'] and report['executed_cases'] == report['expected_cases'] and not report['structural_failures']
    report['status'] = 'completed_needs_human_review' if complete else 'incomplete_or_structural_failure'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(safe_report(report), stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({key: report[key] for key in ('status', 'executed_cases', 'structural_failures', 'logical_budget')}))
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(run(arguments())))
