"""S0/S2 engineering milestone baseline; semantic and S3/S4 acceptance remain pending."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from scripts.memory_evaluation_cases import load_cases,write_report,ROOT


def evidence_evaluate():
    from scripts.memory_evaluation_cases import seed_sources
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.memory.memory_sources import resolve,SECRET
    rows=[]
    cases,checksum=load_cases()
    for case in cases:
        with tempfile.TemporaryDirectory(prefix='nyt-evidence-eval-') as directory:
            root=Path(directory).resolve()
            with ProjectStore(root/'test.sqlite') as store:
                project='11111111-1111-1111-1111-111111111111'
                store.sessions.bind_workspace(project,root)
                store.sessions.create('probe',dict(thread_id='probe',model_id='offline',workspace_root=root,
                    active_tool_names=['memory'],permission_mode='Default'))
                seed_sources(store,'probe',[dict(id='u',role='user',text=case['text'])],at=1000.)
                refs=[dict(session_id='probe',thread_id='probe',message_id='u',revision=1,
                    conversation_revision=0,current_user=False,quote=q,offset=case['text'].index(q)) for q in case['quotes']]
                with store.memory.batch_reader(SECRET) as reader:
                    result=resolve(reader,project,'probe',refs,1001.)
                rows.append(dict(case_id=case['id'],expected=case['expected_spans'],observed=len(result),
                    passed=len(result)==case['expected_spans']))
    paths=list((ROOT/'naiyue_test_agent/application/memory').glob('*.py'))
    return dict(format=1,synthetic=True,provider_calls=0,fixtures_sha256=checksum,cases=rows,
        head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        scope='S0/S2 engineering milestone',semantic_status='semantic_pending',S3='pending',S4='pending')


def evaluate(split='calibration',fixtures=None,protocol=None):
    from scripts.memory_evaluation_cases import load_suite
    from scripts.memory_evaluation_runner import execute_case
    cases,config=load_suite(fixtures,protocol)
    selected=[c for c in cases if c['split']==split or split=='all']
    rows=[]
    for case in selected:
        try:rows.append(execute_case(case))
        except Exception as error:
            rows.append(dict(case_id=case['case_id'],suite=case['suite'],execution_status='failed',
                error_type=type(error).__name__,passed=False))
    manifest=evidence_manifest()
    return dict(format=2,synthetic=True,provider_calls=0,fixtures_sha256=config['suite_fixtures_sha256'],
        protocol_sha256=hashlib.sha256(Path(protocol or ROOT/'tests/data/memory_optimization/protocol.json').read_bytes()).hexdigest(),
        split=split,cases=rows,manifest=manifest,semantic_status='semantic_pending',S3='pending',S4='pending',
        completed=sum(r['execution_status']=='completed' for r in rows),
        passed=sum(r['passed'] is True for r in rows),total=len(rows))


def evidence_manifest():
    paths=sorted(set((ROOT/'naiyue_test_agent/application/memory').glob('*.py')) |
        set((ROOT/'naiyue_test_agent/storage/memory_sql').glob('*.py')) |
        set((ROOT/'naiyue_test_agent/model_access').rglob('*.py')) |
        {ROOT/'scripts/memory_foreground_evaluation.py',ROOT/'scripts/memory_legacy_recall.py',ROOT/'scripts/memory_space_time_probe.py',ROOT/'scripts/memory_space_time_labels.py',
         ROOT/'naiyue_test_agent/tools/memory.py',ROOT/'naiyue_test_agent/tools/memory_writes.py',
         ROOT/'naiyue_test_agent/tools/memory_actions.py',ROOT/'naiyue_test_agent/tools/permission_rules.py',
         ROOT/'naiyue_test_agent/tools/permissions.py',ROOT/'naiyue_test_agent/agent_runtime/tool_execution/permissions.py',
         ROOT/'naiyue_test_agent/agent_runtime/tool_execution/authorization.py',ROOT/'naiyue_test_agent/tools/schemas.py',
         ROOT/'naiyue_test_agent/tools/registry.py',ROOT/'scripts/component_fixtures.py',
         ROOT/'naiyue_test_agent/application/service_application.py',ROOT/'naiyue_test_agent/application/projects/host_assembly.py',
         ROOT/'naiyue_test_agent/storage/memory.py',ROOT/'naiyue_test_agent/storage/migrations.py',
         ROOT/'naiyue_test_agent/storage/personal_database.py',ROOT/'naiyue_test_agent/settings.py'} |
        {ROOT/'naiyue_test_agent/memory_settings.py',ROOT/'naiyue_test_agent/agent_runtime/model_usage_observation.py',ROOT/'scripts/memory_evaluation_cases.py',ROOT/'scripts/memory_evaluation_runner.py',ROOT/'scripts/memory_recall_evaluation.py',ROOT/'scripts/memory_worker_evaluation.py',ROOT/'scripts/review_memory_evaluation.py',ROOT/'scripts/memory_review_metrics.py',ROOT/'scripts/memory_evaluation_costs.py',ROOT/'scripts/memory_coverage_evaluation.py',ROOT/'scripts/probe_memory_semantics.py',Path(__file__).resolve()})
    product=ast.parse((ROOT/'naiyue_test_agent/tools/memory.py').read_text())
    actions_required=any(isinstance(node,ast.ImportFrom) and node.module=='memory_actions'
        for node in ast.walk(product))
    action_path=ROOT/'naiyue_test_agent/tools/memory_actions.py'
    absent=[]
    if not actions_required and not action_path.exists():
        paths.remove(action_path)
        absent.append(str(action_path.relative_to(ROOT)))
    return dict(head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        absent_legacy_sources=absent,
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def compare_reports(current,baseline):
    for key in ('fixtures_sha256','protocol_sha256','split'):
        if not current.get(key) or current[key]!=baseline.get(key):raise ValueError('baseline_identity_mismatch')
    if not baseline.get('manifest',{}).get('source_sha256'):raise ValueError('baseline_manifest_missing')
    old={r['case_id']:r for r in baseline['cases']}
    if set(old)!={r['case_id'] for r in current['cases']}:raise ValueError('baseline_cases_mismatch')
    regressions=[r['case_id'] for r in current['cases'] if old[r['case_id']]['passed'] is True and r['passed'] is not True]
    return dict(regressions=regressions,engineering_nonregression=not regressions,
        semantic_status='semantic_pending',overall='pending_full_acceptance')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stage',choices=('baseline','evaluate'),default='baseline')
    parser.add_argument('--split',choices=('calibration','validation','holdout','all'),default='calibration')
    parser.add_argument('--fixtures',type=Path)
    parser.add_argument('--protocol',type=Path)
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():parser.error('output must not exist')
    if args.stage=='evaluate' and args.baseline is None:parser.error('evaluate requires baseline')
    baseline=json.loads(args.baseline.read_text()) if args.baseline else None
    result=evaluate(args.split,args.fixtures,args.protocol)
    if baseline:result['comparison']=compare_reports(result,baseline)
    write_report(args.output,result)
    print(json.dumps({k:result[k] for k in ('total','completed','passed','semantic_status')}))
    return int(any(r['execution_status']=='failed' for r in result['cases']) or
        args.stage=='evaluate' and (any(r['passed'] is False for r in result['cases']) or
        not result['comparison']['engineering_nonregression']))


if __name__=='__main__':raise SystemExit(main())
