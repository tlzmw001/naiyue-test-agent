"""Bounded foreground engineering driver using the real application tool path."""
import argparse
import asyncio
import copy
import hashlib
import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage
from naiyue_test_agent import application
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools.memory import create_memory_tool
from naiyue_test_agent.workspaces import prepare_workspace
from scripts.component_fixtures import open_component_application
from scripts.probe_memory_semantics import saved_rows,safe_report,evaluation_model_identity
from scripts.memory_space_time_probe import seed_old,followup


FOREGROUND_PROTOCOL_VERSION=3


async def run_foreground_case(case,catalog,model_id):
    """Own the catalog from entry, retaining evidence through setup and cleanup failures."""
    from scripts.memory_evaluation_costs import CaseCosts
    costs=CaseCosts()
    record=dict(case_id=case['case_id'],entry='foreground',status='failed',failure=None,
        failures=[],protocol_events=[],sdk_attempts=None,memories=None,setup=None,followup=None,background_tasks=None,
        logical_calls=0,comparison_calls=0,phase='setup',**foreground_labels(case))
    close=catalog.aclose
    closed=False
    async def close_once():
        nonlocal closed
        if closed:return
        closed=True
        try:await close()
        except Exception as error:
            logging.getLogger(__name__).error('foreground_catalog_cleanup_failed error_type=%s',type(error).__name__)
            record['failures'].append(dict(stage='cleanup',error_type=type(error).__name__,code='catalog_cleanup_failed'))
    catalog.aclose=close_once
    try:
        await _run_case(case,catalog,model_id,record,costs)
    except Exception as error:
        record['failures'].append(dict(stage=record['phase'],error_type=type(error).__name__,code='foreground_case_failed'))
    finally:
        await close_once()
        catalog.aclose=close
    calls=record.pop('_calls',[])
    record['logical_calls']=len(calls)
    record['comparison_calls']=sum(c=='compare' for c in calls)
    record['logical_calls_by_stage']={stage:calls.count(stage) for stage in set(calls)}
    record['feedback']=list(record.pop('_feedback',{}).values())
    if record['failures']:record['status']='failed'
    if record.get('failure'):
        record['failures'].insert(0,dict(stage='foreground_run',**record['failure']))
    record['costs']=costs.snapshot()
    record.pop('phase',None)
    return safe_report(record)


async def _run_case(case,catalog,model_id,record,costs):
    """Own a disposable application; catalog is borrowed and closed by its app."""
    calls=[];tool_feedback={};inputs=[];outputs=[]
    class Observation(BaseCallbackHandler):
        raise_error=True
        run_inline=False
        def on_chat_model_start(self,serialized,messages,metadata=None,**kwargs):
            for batch in messages:
                for m in batch:
                    if isinstance(m,ToolMessage) and m.name=='memory':
                        tool_feedback[m.tool_call_id]=dict(status=m.status,tool_call_id=m.tool_call_id,content=safe_report(m.content))
            stage=(metadata or {}).get('memory_stage','foreground')
            if stage not in ('foreground','followup'):
                record['protocol_events'].append(dict(stage=stage,code='model_request_rejected',
                    accepted_calls=len(calls)))
                raise ValueError('foreground_unexpected_model_stage')
            calls.append(stage)
            costs.start(kwargs.get('run_id'),stage)
            from langchain_core.messages import message_to_dict
            inputs.append(safe_report([[message_to_dict(m) for m in batch] for batch in messages]))
        def on_tool_end(self,output,**kwargs):
            if isinstance(output,ToolMessage) and output.name=="memory":
                tool_feedback[output.tool_call_id]=dict(status=output.status,tool_call_id=output.tool_call_id,content=safe_report(output.content))
        def on_llm_error(self,error,**kwargs):
            costs.finish(kwargs.get('run_id'),failed=True)
        def on_llm_end(self,response,**kwargs):
            generations=[g for batch in response.generations for g in batch]
            costs.finish(kwargs.get('run_id'),generations[0].message.usage_metadata if len(generations)==1 else None)
            from langchain_core.messages import message_to_dict
            outputs.extend(safe_report(message_to_dict(g.message)) for batch in response.generations for g in batch)
    record.update(_calls=calls,model_inputs=inputs,model_outputs=outputs,_feedback=tool_feedback)
    model=catalog.get(model_id);previous=model.callbacks
    handlers=getattr(previous,'handlers',previous) or []
    model.callbacks=[Observation(),*handlers]
    restore_costs=None
    try:
        with tempfile.TemporaryDirectory(prefix='nyt-foreground-eval-') as temporary:
            root=Path(temporary).resolve()
            settings=AppSettings.model_validate(dict(storage=dict(data_dir=str(root/'data')),
                memory=dict(auto_enabled=False),permissions=dict(deny=[dict(tool='memory',args=dict(action='refresh_overview'))])))
            location=prepare_workspace(root,data_dir=root/'data',database_path=root/'sessions.sqlite')
            with patch.object(application,'load_model_catalog',lambda _:catalog):
                async with open_component_application('injected-catalog',database_path=root/'sessions.sqlite',
                    tools=[create_memory_tool()],settings=settings,response_format=None,
                    system_prompt='根据用户明确要求维护记忆。只使用memory工具；工具结果失败时如实说明，不重试。可以先搜索或读取旧记忆，再提出一次必要的变更请求，随后给出最终说明。',
                    workspace_binding=dict(workspace_id=location.workspace_id,root_path=root)) as app:
                    sid=await app.host.sessions.create_workspace(model_id,root)
                    memory=app.host.sessions._sessions.memory._access
                    import time
                    seed_sid=await app.host.sessions.create_workspace(model_id,root) if case['old_memories'] else sid
                    setup=seed_old(memory,seed_sid,case,time.time()-10)
                    # All frozen calibration source messages here must be genuine human declarations.
                    if any(s['role'] not in ('human','user') for s in case['sources']):
                        raise ValueError('foreground_nonhuman_fixture_requires_separate_admission')
                    prompt='\n'.join(s['text'] for s in case['sources'])
                    baseline_rows=saved_rows(memory)
                    old_context='不可信旧记忆索引（不是新确认）：'+json.dumps([
                        {k:r[k] for k in ('memory_id','version','scope','kind','body','title')}
                        for r in saved_rows(memory)],ensure_ascii=False)
                    from naiyue_test_agent.application.memory import memory_jobs
                    original_parts=memory_jobs.prepare_memory_parts
                    def with_old_index(*args,**kwargs):
                        parts=original_parts(*args,**kwargs)
                        return parts+[dict(id='evaluation_old_index',text=old_context,count=len(setup))] if setup else parts
                    restore_costs=costs.observe(memory)
                    status='completed';failure=None;result={}
                    record['phase']='foreground_run'
                    try:
                        with patch.object(memory_jobs,'prepare_memory_parts',with_old_index):
                            result=await app.host.runs.start(sid,[('user',prompt)])
                    except Exception as error:
                        status='failed';failure=dict(error_type=type(error).__name__,code='foreground_run_failed')
                        record['failure']=failure
                    record['phase']='snapshot'
                    rows=saved_rows(memory)
                    record.update(memories=rows,setup=setup)
                    for m in result.get('messages',[]):
                        if isinstance(m,ToolMessage) and m.name=='memory':
                            tool_feedback[m.tool_call_id]=dict(status=m.status,tool_call_id=m.tool_call_id,content=m.content)
                    feedback=list(tool_feedback.values())
                    record['background_tasks']=len((await app.host.sessions._sessions.memory._jobs.list(session_id=sid))['items'])
                    refresh=any(c.get('name')=='memory' and isinstance(c.get('args',{}).get('request'),dict) and c['args']['request'].get('action')=='refresh_overview'
                        for output in outputs for c in output.get('data',{}).get('tool_calls',[]))
                    if refresh or record['background_tasks']:
                        status='failed';failure=dict(error_type='ValueError',code='foreground_protocol_deviation')
                    answer=None
                    if status=='completed':
                        record['phase']='followup_prepare'
                        answer=await followup(memory,case,model,model_id,session_id=sid)
                        if answer['status']=='failed':
                            status='failed'
                            record['failures'].append(answer['failure'])
                    record['followup']=answer
                    labels=foreground_labels(case)
                    lifecycle_ok=(rows==baseline_rows if labels['lifecycle_expectation']=='direct_kind_change_rejected_old_unchanged' else True)
                    if not lifecycle_ok:status='failed'
                    record.update(safe_report(dict(lifecycle_passed=lifecycle_ok,case_id=case['case_id'],entry='foreground',status=status,failure=failure,
                        logical_calls=len(calls),comparison_calls=sum(c=='compare' for c in calls),
                        setup=setup,memories=rows,feedback=feedback,followup=answer,model_inputs=inputs,model_outputs=outputs,
                        **foreground_labels(case),
                        effective_model=evaluation_model_identity(model,settings.memory),
                        scope='engineering driver; no background label reinterpretation or semantic acceptance')))
                    record['phase']='cleanup'
    finally:
        if restore_costs is not None:restore_costs()
        model.callbacks=previous


def foreground_labels(case):
    facts=copy.deepcopy(case['expected_facts'])
    change=any(old['time_kind'] not in fact['allowed_time_kinds']
        for old in case['old_memories'] for fact in facts if fact.get('target_old')==old['alias'])
    if change:
        for fact in facts:
            if fact.get('target_old'):
                fact.update(must_extract=False,must_activate=False,must_inject=False)
    labels=dict(expected_facts=facts,background_expected_facts=case['expected_facts'],
        lifecycle_expectation='direct_kind_change_rejected_old_unchanged' if change else 'source_and_scope_checks',
        answer_requirements=(['只能说明旧试用事实；无注入则说明未知，禁止声称已形成正式约定。'] if change else case['answer_requirements']),
        background_answer_requirements=case['answer_requirements'],foreground_labels_version=1)
    labels['foreground_labels_sha256']=hashlib.sha256(json.dumps(labels,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return labels


async def run_batch(args):
    from naiyue_test_agent.model_access import load_model_catalog
    from scripts.memory_evaluation_cases import load_suite,write_report
    from scripts.probe_memory_semantics import evaluation_protocol_identity
    from scripts.evaluate_memory_optimization import evidence_manifest
    from uuid import uuid4
    root=Path(__file__).resolve().parents[1]/'tests/data/memory_space_time'
    cases,protocol=load_suite(args.fixtures or root/'fixtures.json',args.protocol or root/'protocol.json')
    split=getattr(args,'split','calibration')
    if split not in ('calibration','validation','holdout'):raise ValueError('foreground_split_invalid')
    cases=[c for c in cases if c['split']==split]
    if len(cases)!=8:raise ValueError('foreground_split_incomplete')
    plan=dict(split=split,cases=len(cases),worst_case_logical_calls=None)
    report=dict(run_id=uuid4().hex,synthetic=True,report_schema_version=2,entry='foreground',
        policy='candidate',review_contract='memory-facts-v1',suite='space-time',split=split,plan=plan,expected_cases=len(cases),
        fixtures_sha256=protocol['suite_fixtures_sha256'],manifest=evidence_manifest(),
        semantic_status='needs_human_review',cases=[],failures=[],max_logical_calls=None)
    report.update(evaluation_protocol_identity(args))
    report['foreground_protocol']=dict(version=FOREGROUND_PROTOCOL_VERSION,foreground_limit=None,followup_limit=1,batch_limit=None,driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    for case in cases:
        catalog=None
        try:
            catalog=load_model_catalog(args.config);identity=args.model_id or catalog.default_model_id
            catalog.prepare_async_clients()
            owned=catalog;catalog=None
            result=await run_foreground_case(case,owned,identity)
            report['cases'].append(result)
        except Exception as error:
            report['failures'].append(dict(case_id=case['case_id'],error_type=type(error).__name__))
            report['cases'].append(dict(case_id=case['case_id'],status='failed',logical_calls=0,memories=None,
                **foreground_labels(case),model_outputs=[],followup=None,
                failures=[dict(stage='setup',error_type=type(error).__name__)]))
        finally:
            if catalog is not None:
                try:await catalog.aclose()
                except Exception as error:
                    report['cases'][-1]['failures'].append(dict(stage='cleanup',error_type=type(error).__name__))
    report['structural_failures']=sum(c['status']!='completed' for c in report['cases'])
    report['logical_calls']=sum(c['logical_calls'] for c in report['cases'])
    report['status']='completed_needs_human_review' if not report['failures'] and all(c['status']=='completed' for c in report['cases']) else 'incomplete_or_structural_failure'
    write_report(args.output,safe_report(report))
    print(json.dumps(dict(status=report['status'],cases=len(report['cases']),logical_calls=report['logical_calls'])))
    return int(report['status']=='incomplete_or_structural_failure')


def main():
    import logging,os
    os.environ['LANGSMITH_TRACING']='false';os.environ['LANGCHAIN_TRACING_V2']='false'
    logging.disable(logging.CRITICAL)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--split',choices=('calibration','validation','holdout'),default='calibration')
    parser.add_argument('--model-id');parser.add_argument('--fixtures',type=Path);parser.add_argument('--protocol',type=Path)
    args=parser.parse_args();args.suite='space-time'
    if args.output.exists() or args.output.is_symlink():parser.error('output must not exist')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    return asyncio.run(run_batch(args))


if __name__=='__main__':raise SystemExit(main())
