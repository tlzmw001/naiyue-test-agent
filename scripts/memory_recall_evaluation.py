"""Synthetic multi-item calibration through the production recall entrypoint."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time
from uuid import uuid4
from scripts.memory_evaluation_cases import ROOT,write_report


def execute(case):
    from scripts.memory_evaluation_cases import seed_sources
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.application.memory.memory_batches import build_batch,bind_proposal_evidence
    from scripts.memory_legacy_recall import prepare_memory_parts
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.tools.memory import MemoryInput
    from langchain_core.messages import HumanMessage
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='nyt-recall-calibration-') as directory:
        root=Path(directory).resolve()
        with ProjectStore(root/'sessions.sqlite') as store:
            project=str(uuid4());store.sessions.bind_workspace(project,root)
            store.sessions.create('probe',dict(thread_id='probe',model_id='offline',workspace_root=root,
                active_tool_names=['memory'],permission_mode='Default'))
            memory=MemoryAccess(store,MemorySettings(auto_enabled=False,personal_convention_tokens=0),root/'data')
            try:
                seed_sources(store,'probe',[dict(id=s['alias'],role='user',text=s['text']) for s in case['seeds']],at=time.time()-100)
                b=build_batch(store.memory,session_id='probe',thread_id='probe',conversation_revision=0,start=(0,0),upper=100,
                    token_budget=16000,source_chars=4096,max_messages=128,max_read_chars=65536,overlap_chars=600,context_floor=1)
                ids={}
                for s in case['seeds']:
                    eid=next(u['evidence_id'] for u in b['units'] if u['message_id']==s['alias'])
                    refs=bind_proposal_evidence(dict(sources=[dict(evidence_id=eid)]),b)['sources']
                    request=MemoryInput(action='record',scope='workspace',sources=refs,proposal=dict(kind='context',
                        title=s['text'],subject=s['alias'],body=s['text'],basis='user_declaration',intent='activate',reason='合成校准种子'))
                    ids[s['alias']]=memory.query('probe',project,request.model_dump(mode='json'))['item']['memory_id']
                before=store.database.connection.total_changes
                parts=prepare_memory_parts(memory,'probe',project,[HumanMessage(content=case['query'])],None)
                injected=[r['memory_id'] for p in parts if p['id']=='memory' for r in json.loads(p['text'][p['text'].index('{'):])['related']]
                expected={ids[a] for a in case['expected_aliases']};actual=set(injected);hit=len(expected&actual)
                delta=store.database.connection.total_changes-before
                return dict(case_id=case['case_id'],passed=actual==expected and len(injected)==len(actual) and delta==0,
                    expected_ids=sorted(expected),injected_ids=injected,missed=sorted(expected-actual),
                    false_injections=sorted(actual-expected),recall=hit/len(expected) if expected else None,
                    precision=hit/len(actual) if actual else None,correct_empty=not actual if not expected else None,
                    write_delta=delta,duration_ms=(time.monotonic()-started)*1000)
            finally:memory.close()


def evaluate(split='calibration'):
    from scripts.evaluate_memory_optimization import evidence_manifest
    from scripts.memory_evaluation_cases import load_engineering_matrix
    cases,checksum,protocol_hash=load_engineering_matrix('recall',split)
    return dict(run_id=uuid4().hex,provider_calls=0,synthetic=True,split=split,fixtures_sha256=checksum,
        protocol_sha256=protocol_hash,manifest=evidence_manifest(),overall='pending_baseline_cost_and_holdout',
        cases=[execute(c) for c in cases])


def compare(current,baseline):
    from scripts.evaluate_memory_optimization import compare_reports
    result=compare_reports(current,baseline)
    old={r['case_id']:r for r in baseline['cases']}
    result['metric_regressions']=[r['case_id']+':'+metric for r in current['cases'] for metric in ('recall','precision')
        if old[r['case_id']].get(metric) is not None and (r.get(metric) is None or r[metric]<old[r['case_id']][metric])]
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--split',choices=('calibration','validation','holdout'),default='calibration')
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():parser.error('output must not exist')
    report=evaluate(args.split)
    if args.baseline:report['comparison']=compare(report,json.loads(args.baseline.read_text()))
    write_report(args.output,report)
    print(json.dumps(dict(total=len(report['cases']),passed=sum(c['passed'] for c in report['cases']),provider_calls=0)))
    return int(not all(c['passed'] for c in report['cases']) or bool(report.get('comparison',{}).get('regressions'))
        or bool(report.get('comparison',{}).get('metric_regressions')))


if __name__=='__main__':raise SystemExit(main())
