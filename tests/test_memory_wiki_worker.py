"""Real queue -> extraction -> bounded Wiki reads -> atomic commit, without network."""
import asyncio
import json
import time
import pytest
from langchain_core.messages import AIMessage
from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
from naiyue_test_agent.model_access import ModelCatalog
from test_memory_jobs import jobs,response
from test_memory_lifecycle import source
from test_async_application import StreamingToolModel


def msg(value):return AIMessage(content=json.dumps(value,ensure_ascii=False))


def read(request):return msg(dict(result=dict(step='read',requests=[{k:v for k,v in request.items() if k!='scope'}])))


def finish(**changes):return msg(dict(result=dict(step='finish',decisions=[],page_changes=[],links=[])|changes))


@pytest.mark.parametrize('failure',[None,'read','bad_proposal','repeat','secret'])
def test_wiki_worker_real_lifecycle(jobs,failure):
    async def run():
        storage,access,_,root=jobs
        storage.memory.prepare_wiki(now=time.time())
        proposal=dict(matter='测试环境',topic='working_agreements',scope='workspace',title='测试环境',subject='pytest环境',
            body='使用.venv',basis='user_declaration',intent='activate',reason='长期约定',sources=[dict(evidence_id='E1')])
        if failure=='secret':proposal['body']='Authorization: Bearer private-secret-value'
        extraction=response(proposals=[proposal if failure!='bad_proposal' else dict(proposal,sources=[dict(evidence_id='E999')])])
        directory=dict(action='list_pages',scope='workspace',topic='working_agreements')
        replies=[extraction]
        if failure not in ('bad_proposal','secret'):
            replies.append(read(dict(action='read_page',scope='workspace',page_id='a'*32) if failure=='read' else directory))
            if failure=='repeat':replies.append(read(directory))
            elif failure is None:
                replies.append(finish(decisions=[dict(relation='add',proposal_ids=['P1'],destination='N1',evidence_ids=['E1'])],
                    page_changes=[dict(ref='N1',topic='working_agreements',title='执行环境',description='运行约定')]))
        model=StreamingToolModel(replies=replies)
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            source(access,'s',text='以后测试始终使用.venv',at=time.time()-1)
            result=await service.request(source_session_id='s',target_session_id='s',workspace_root=root,
                origin_run_id='R',model_id='A')
            assert not model.seen
            await service.after_finish('s','R','completed',model_id='A');await service.wait_idle()
            job=service._entries[result['task_id']]
            assert job.cleanup_confirmed and job.overview_saved==1
            rows=storage.database.connection.execute('SELECT body,status FROM naiyue_memories').fetchall()
            if failure is None:
                assert job.result_status=='completed' and [tuple(r) for r in rows]==[('使用.venv','active')]
                assert len(model.seen)==3
            else:
                assert job.result_status=='partial' and rows==[] and len(model.seen)==len(replies)
            assert model.replies==[]
        finally:await service.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('relation',['supplement','confirm','correction','duplicate','unsupported'])
def test_worker_reads_old_page_before_update(jobs,relation):
    from test_memory_wiki_mutations import args
    async def run():
        storage,access,_,root=jobs;now=time.time()
        workspace=storage.sessions.get_workspace_binding()['workspace_id']
        storage.memory.prepare_wiki(now=now)
        oldsource=source(access,'s',text='以后使用.venv',at=now-100)
        item=access.query_wiki('s',workspace,args(sources=[oldsource]),now=now)['item']
        source(access,'s',text='完整确认之前规则继续适用',at=now-1)
        p=dict(args(sources=[])['proposal'],matter='测试环境',topic='working_agreements',scope='workspace',sources=[dict(evidence_id='E2')])
        decision=dict(relation=relation,proposal_ids=['P1'])
        if relation!='unsupported':decision.update(old_refs=['O1'],target_old_ref='O1')
        if relation not in ('duplicate','unsupported'):decision.update(evidence_ids=['E2'],confirmation_scope='whole')
        if relation=='correction':decision['proposal']=dict(args(sources=[])['proposal'],body='使用uv')
        model=StreamingToolModel(replies=[response(proposals=[p]),
            read(dict(action='list_pages',scope='workspace',topic='working_agreements')),
            read(dict(action='read_page',scope='workspace',page_id=item['page_id'])),finish(decisions=[decision])])
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            result=await service.request(source_session_id='s',target_session_id='s',workspace_root=root,origin_run_id='R',model_id='A')
            await service.after_finish('s','R','completed',model_id='A');await service.wait_idle()
            job=service._entries[result['task_id']]
            assert job.result_status=='completed' and job.overview_saved==1 and len(model.seen)==4
            row=storage.database.connection.execute('SELECT * FROM naiyue_memories').fetchone()
            assert row['body']==('使用uv' if relation=='correction' else '使用.venv')
            if relation in ('confirm','correction'):assert row['evidence_at']==now-1
            else:assert row['evidence_at']==now-100
            assert row['version']==(1 if relation in ('duplicate','unsupported') else 2)
        finally:await service.aclose()
    asyncio.run(run())


def test_stop_organization_waiting_on_model_never_writes(jobs):
    async def run():
        storage,access,_,root=jobs;storage.memory.prepare_wiki(now=time.time())
        from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
        from memory_wiki_helpers import record
        workspace=storage.sessions.get_workspace_binding()['workspace_id'];p=WikiWriter.new_page('collaboration','习惯','')
        with storage.memory.wiki_edit('workspace',workspace,now=time.time(),capacity=30) as writer:
            writer.apply(pages=[p],memories=[record(p['page_id'],workspace_id=workspace,expires_at=time.time()+86400)])
        entered=asyncio.Event();release=asyncio.Event()
        model=StreamingToolModel(replies=[finish()],entered=entered,release=release)
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            result=await service.request(source_session_id='s',target_session_id='s',workspace_root=root,origin_run_id='R',model_id='A',
                wiki_request=dict(action='organize',scope='workspace',page_ids=[p['page_id']]))
            await service.after_finish('s','R','completed',model_id='A');await asyncio.wait_for(entered.wait(),5)
            job=service._entries[result['task_id']];job.control.request('user')
            await service.wait_idle()
            assert job.result_status=='stopped' and job.saved==0 and len(model.seen)==1
            assert storage.database.connection.execute('SELECT version FROM naiyue_memories').fetchone()[0]==1
        finally:release.set();await service.aclose()
    asyncio.run(run())


def test_independent_topics_commit_without_invalidating_each_other(jobs):
    async def run():
        storage,access,_,root=jobs;storage.memory.prepare_wiki(now=time.time())
        proposals=[];replies=[]
        for i,topic in enumerate(('working_agreements','stable_knowledge'),1):
            proposals.append(dict(matter=f'事项{i}',topic=topic,scope='workspace',title=f'事项{i}',subject=f'事项{i}',
                body=f'规则{i}',basis='user_declaration',intent='activate',reason='长期',sources=[dict(evidence_id='E1')]))
            replies.extend([read(dict(action='list_pages',scope='workspace',topic=topic)),
                finish(decisions=[dict(relation='add',proposal_ids=[f'P{i}'],destination='N1',evidence_ids=['E1'])],
                    page_changes=[dict(ref='N1',topic=topic,title=f'主题{i}',description='')])])
        model=StreamingToolModel(replies=[response(proposals=proposals),*replies])
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            source(access,'s',text='两条长期规则',at=time.time()-1)
            result=await service.request(source_session_id='s',target_session_id='s',workspace_root=root,origin_run_id='R',model_id='A')
            await service.after_finish('s','R','completed',model_id='A');await service.wait_idle()
            job=service._entries[result['task_id']]
            assert job.result_status=='completed' and job.saved==2 and len(model.seen)==5
            assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==2
        finally:await service.aclose()
    asyncio.run(run())


def test_independent_same_topic_valid_decision_survives_bad_destination(jobs):
    async def run():
        storage,access,_,root=jobs;storage.memory.prepare_wiki(now=time.time())
        p=dict(matter='测试环境',topic='working_agreements',scope='workspace',title='测试环境',subject='pytest环境',
            body='使用.venv',basis='user_declaration',intent='activate',reason='长期约定',sources=[dict(evidence_id='E1')])
        model=StreamingToolModel(replies=[response(proposals=[p,dict(p,matter='报告',subject='报告',body='保存报告')]),
            read(dict(action='list_pages',scope='workspace',topic='working_agreements')),
            finish(decisions=[dict(relation='add',proposal_ids=[pid],destination=dest,evidence_ids=['E1']) for pid,dest in [('P1','N1'),('P2','N2')]],
                page_changes=[dict(ref='N1',topic='working_agreements',title='环境',description='')])])
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            source(access,'s',text='以后使用.venv并保存报告',at=time.time()-1)
            result=await service.request(source_session_id='s',target_session_id='s',workspace_root=root,origin_run_id='R',model_id='A')
            await service.after_finish('s','R','completed',model_id='A');await service.wait_idle()
            job=service._entries[result['task_id']]
            assert job.result_status=='partial' and job.saved==1
            assert [r['body'] for r in storage.database.connection.execute('SELECT body FROM naiyue_memories')]==['使用.venv']
        finally:await service.aclose()
    asyncio.run(run())
