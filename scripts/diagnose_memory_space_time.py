"""Replay frozen synthetic outputs to locate deterministic memory failures."""
import argparse,asyncio,json
from pathlib import Path
from unittest.mock import patch
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult,ChatGeneration
from pydantic import Field
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.application.memory import memory_comparison as compare
from naiyue_test_agent.application.memory import memory_job_runner as runner
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from scripts.probe_memory_semantics import run_case,probe_callback,selected_cases,arguments
from scripts.provider_acceptance_budget import RequestBudget
class Model(BaseChatModel):
    replies:list=Field(default_factory=list)
    @property
    def _llm_type(self):return 'offline-recorded-replay'
    def bind_tools(self,*args,**kwargs):return self
    def _generate(self,*a,**kw):raise AssertionError('async only')
    async def _agenerate(self,*a,**kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.replies.pop(0)))])
async def main():
    parser=argparse.ArgumentParser(description='Offline replay of synthetic S5 model results; no provider calls')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.output.is_symlink():raise FileExistsError(args.output)
    report=json.loads(args.report.read_text())
    if report.get('synthetic') is not True or report.get('suite')!='space-time':raise ValueError('synthetic_space_time_report_required')
    selection=arguments(['--config','unused','--suite','space-time','--split',report['split'],'--output',str(args.output)])
    cases={c['id']:c for c in selected_cases(selection)[0]};results=[]
    for old in report['cases']:
        cid=old['case_id']
        if any('parse_error_type' in o for o in old['model_outputs']):
            results.append(dict(case_id=cid,status='unreplayable_raw_failed_output_not_recorded'));continue
        replies=[json.dumps(o['parsed'],ensure_ascii=False) for o in old['model_outputs'] if 'parsed' in o]
        replies.append('离线回答占位，仅检查处理链，不评语义。')
        model=Model(replies=replies);catalog=ModelCatalog('offline',{'offline':model})
        settings=MemorySettings(auto_enabled=False,max_batches=1);observer=probe_callback(RequestBudget(3),settings);model.callbacks=[observer]
        evidence=[];reads=[]
        def observed(fn,label):
            def call(*a,**kw):
                try:return fn(*a,**kw)
                except Exception as e:
                    causes=[];current=e
                    while current is not None:
                        causes.append(dict(type=type(current).__name__,detail=str(current)[:1400]));current=current.__cause__
                    evidence.append(dict(stage=label,causes=causes));raise
            return call
        query=MemoryAccess.query
        def read(self,sid,pid,request,**kw):
            result=query(self,sid,pid,request,**kw)
            if request['action'] in ('search','list'):
                reads.append(dict(action=request['action'],kind=request.get('kind'),scope=request['scope'],query=request.get('query'),items=len(result.get('items',[]))))
            return result
        try:
            with patch.object(compare,'bind_extraction',observed(compare.bind_extraction,'bind_extraction')),patch.object(runner,'compile_comparison_decisions',observed(runner.compile_comparison_decisions,'compile_comparison')),patch.object(MemoryAccess,'query',read):
                result=await run_case(cases[cid],1,catalog,'offline',observer,settings)
            results.append(dict(case_id=cid,status=result['task']['result_status'],errors=evidence,reads=reads,injected=result['followup']['injected_ids']))
        finally:await catalog.aclose()
    from scripts.memory_evaluation_cases import write_report
    write_report(args.output,dict(provider_calls=0,cases=results))
    print(json.dumps(dict(provider_calls=0,replayed=sum(c['status']!='unreplayable_raw_failed_output_not_recorded' for c in results),total=len(results))))
if __name__=='__main__':asyncio.run(main())
