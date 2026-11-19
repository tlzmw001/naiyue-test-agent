"""Exploratory recall comparison on disposable synthetic stores; no model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
from unittest.mock import patch

from langchain_core.messages import HumanMessage
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from scripts import memory_legacy_recall as memory_context
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.memory_sql.writes import MemoryWriter,FIELDS
from scripts.memory_evaluation_cases import write_report

# Explicit synthetic distractors: each addresses a different topic from the target.
DISTRACTORS=[
 ('发布安排','本项目下周发布安排需要先确认版本号。'),
 ('缓存测试','本项目缓存测试使用独立 Redis 实例。'),
 ('生产网关','生产网关的域名证书每季度检查。'),
 ('网关超时','网关超时需检查连接池和网络日志，尚未确认原因。'),
 ('支付数据库','支付数据库的只读副本用于报表查询。'),
 ('本地编辑器','本地编辑器使用 UTF-8 编码保存文件。'),
 ('代码审查','代码审查时关注错误处理和敏感信息。'),
 ('依赖升级','依赖升级需要检查许可证和兼容性。'),
 ('CI制品','CI 构建制品保留七天。'),
 ('回归账户','回归账户需要单独申请权限。'),
]
MODES=[('current',.5,False),('coverage_03',.3,False),('coverage_02',.2,False),
 ('coverage_01',.1,False),('trim_05',.5,True),('trim_03',.3,True)]


def seed(c,index,row,workspace):
    data=dict.fromkeys(FIELDS)
    data.update(memory_id=f'{index:032x}',version=1,scope=row.get('scope','workspace'),
        workspace_id=workspace if row.get('scope','workspace')=='workspace' else None,
        kind=row.get('kind','context'),status='active',title=row['title'],body=row['body'],
        subject=f'synthetic-{index}',aliases_json=row.get('aliases_json','[]'),tags_json='[]',
        gaps_json='[]',reason='explicit synthetic evaluation setup',basis='user_declaration',
        created_at=1900000000.,updated_at=1900000000.,evidence_at=1900000000.,
        last_confirmed_at=1900000000.,ttl_days=30,expires_at=2100000000.)
    MemoryWriter(c).save(data,[])
    return data['memory_id']


def trim(text):
    return re.sub(r'^(?:请)?(?:说明|解释|分析)','',text).replace('本项目','').replace('当前','').strip()


def execute(case,mode,threshold,trim_query,with_noise):
    with tempfile.TemporaryDirectory(prefix='nyt-offline-recall-') as folder:
        root=Path(folder).resolve();project='11111111-1111-1111-1111-111111111111'
        with ProjectStore(root/'store.sqlite') as store:
            store.sessions.bind_workspace(project,root)
            store.sessions.create('probe',dict(thread_id='probe',model_id='offline',workspace_root=root,
                active_tool_names=['memory'],permission_mode='Default'))
            settings=MemorySettings(auto_enabled=False,fallback_coverage=threshold)
            access=MemoryAccess(store,settings,root/'data')
            try:
                target_ids=[];names={};excluded=set()
                for i,row in enumerate(case['rows'],1):
                    _,owner=access._stores(row['scope'])[0]
                    mid=seed(owner.database.connection,i,row,project);owner.database.connection.commit()
                    target_ids.append(mid);names[mid]='target:'+row['title']
                if with_noise:
                    for i,(title,body) in enumerate(DISTRACTORS,101):
                        mid=seed(store.database.connection,i,dict(title=title,body=body),project)
                        names[mid]='noise:'+title
                    # Exact matching rows in forbidden scope/status must never enter results.
                    for i,change in enumerate([dict(workspace_id='other-project'),dict(status='candidate'),dict(expires_at=1.)],201):
                        mid=seed(store.database.connection,i,dict(title=case['query'],body=case['query']),project)
                        for key,value in change.items():
                            assert key in ('workspace_id','status','expires_at')
                            store.database.connection.execute(f'UPDATE naiyue_memories SET {key}=? WHERE memory_id=?',(value,mid))
                        excluded.add(mid)
                    store.database.connection.commit()
                observed=[];query=access.query
                def read(sid,pid,request):
                    result=query(sid,pid,request)
                    if request['action']=='search':observed.append(dict(query=request['query'],returned=len(result['items'])))
                    return result
                access.query=read
                original=memory_context._query_plan
                def plan(messages,settings):
                    result=original(messages,settings)
                    if trim_query:
                        for q in result['queries']:q['text']=trim(q['text'])
                    return result
                before=sum(owner.database.connection.total_changes for _,owner in access._stores('both'))
                with patch.object(memory_context,'_query_plan',plan):
                    parts=memory_context.prepare_memory_parts(access,'probe',project,[HumanMessage(content=case['query'])],None)
                after=sum(owner.database.connection.total_changes for _,owner in access._stores('both'))
                injected=[]
                for part in parts:
                    if part['id']=='memory':
                        payload=json.loads(part['text'][part['text'].index('{'):])
                        injected=[r['memory_id'] for k in ('personal_conventions','related') for r in payload[k]]
                assert not set(injected)&excluded and after==before
                return dict(case_id=case['id'],mode=mode,noise=with_noise,targets=len(target_ids),
                    hits=len(set(injected)&set(target_ids)),noise_injected=len(set(injected)-set(target_ids)),
                    injected=[names.get(i,i) for i in injected],search=observed,write_delta=after-before,
                    not_stored=not target_ids)
            finally:access.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    source=json.loads(args.report.read_text())
    if source.get('synthetic') is not True:raise ValueError('synthetic report required')
    cases=[dict(id=c['case_id'],query=c['followup']['model_request'][-1]['data']['content'],rows=c['memories']) for c in source['cases']]
    rows=[execute(c,*mode,noise) for noise in (False,True) for mode in MODES for c in cases]
    controls=[execute(dict(id=name,query=query,rows=[]),*mode,True)
        for name,query in [('weak','继续'),('unmatched','解释量子纠缠的实验原理。'),('generic','说明本项目当前安排。')] for mode in MODES]
    summary=[]
    for noise in (False,True):
        for mode,_,_ in MODES:
            selected=[r for r in rows if r['mode']==mode and r['noise']==noise]
            hits=sum(r['hits'] for r in selected);total=sum(r['targets'] for r in selected);wrong=sum(r['noise_injected'] for r in selected)
            summary.append(dict(mode=mode,noise=noise,hits=hits,total=total,recall=hits/total if total else None,
                noise_injected=wrong,target_share=hits/(hits+wrong) if hits+wrong else None))
    write_report(args.output,dict(provider_calls=0,source_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
        experiment='recorded synthetic bodies, refreshed disposable eligibility clocks; no semantic model scoring',
        scope='calibration only; absent records not invented; source-only case07 uses original preserved body',
        distractors=DISTRACTORS,modes=MODES,summary=summary,cases=rows,controls=controls,
        metric_note="Target counts identify seeded records, not official must_inject facts or human semantic precision; calibration-02 absent; 07 original compound retained.",
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
