"""Real evaluation must use the production worker and retain all case outcomes."""
import asyncio,json
from langchain_core.messages import AIMessage
from test_async_application import StreamingToolModel
from test_memory_readonly import memory

from naiyue_test_agent.model_access import ModelCatalog


def test_background_driver_uses_real_queue_without_labels():
    from scripts.memory_wiki_runtime_evaluation import background_case
    from scripts.memory_wiki_evaluation import load_suite
    case=next(c for c in load_suite()[0] if c['case_id']=='W01')
    model=StreamingToolModel(replies=[AIMessage(content=json.dumps(dict(overview=dict(goal='检查',progress='完成',blockers='',next_step=''),proposals=[])))])
    result=asyncio.run(background_case(case,ModelCatalog('A',{'A':model}),'A'))
    assert result['status']=='completed' and result['observations']['overview_saved']==1
    assert result['observations']['memories']==[]
    assert len(model.seen)==1
    assert 'allowed_changes' not in str(model.seen) and 'must_preserve_conditions' not in str(model.seen)


def test_background_driver_prepares_real_old_sources():
    from scripts.memory_wiki_runtime_evaluation import background_case
    from scripts.memory_wiki_evaluation import load_suite
    case=next(c for c in load_suite()[0] if c['case_id']=='W07')
    model=StreamingToolModel(replies=[AIMessage(content=json.dumps(dict(overview=dict(goal='检查',progress='完成',blockers='',next_step=''),proposals=[])))])
    result=asyncio.run(background_case(case,ModelCatalog('A',{'A':model}),'A'))
    assert result['status']=='completed'
    assert result['observations']['overview_saved']==1 and len(result['observations']['memories'])==1


def test_all_calibration_baselines_preserve_pages_and_memory_counts(tmp_path):
    import time
    from uuid import uuid4
    from scripts.memory_wiki_runtime_evaluation import seed_wiki
    from scripts.memory_wiki_evaluation import load_suite
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.memory_settings import MemorySettings
    for case in (c for c in load_suite()[0] if c['split']=='calibration'):
        root=tmp_path/case['case_id'];root.mkdir()
        with ProjectStore(root/'s.sqlite') as store:
            workspace=str(uuid4());store.sessions.bind_workspace(workspace,root);store.memory.prepare_wiki(now=time.time())
            store.sessions.create('old',dict(thread_id='old',model_id='test',workspace_root=root,active_tool_names=['memory'],permission_mode='Default'))
            access=MemoryAccess(store,MemorySettings(),root/'data')
            try:
                pages=seed_wiki(access,'old',case['input'],time.time())
                assert len(pages)==len(case['input']['wiki']['pages'])
                count=sum(r.database.read_rows('SELECT COUNT(*) AS n FROM naiyue_memories',())[0]['n'] for _,r in access._stores('both'))
                assert count==len(case['input']['wiki']['memories'])
                for original in case['input']['wiki']['pages']:
                    assert pages[original['page_id']][1]['description']==original.get('description','')
            finally:access.close()


def test_background_read_scope_is_bound_by_code_not_model(memory):
    from naiyue_test_agent.application.memory.memory_wiki_protocol import OrganizationResult
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    from test_memory_wiki_mutations import setup
    _,access,workspace=setup(memory)
    parsed=OrganizationResult.model_validate(dict(result=dict(step='read',requests=[dict(action='list_pages',topic='working_agreements')])))
    request=parsed.result.requests[0].model_dump(mode='json')
    assert 'scope' not in request
    result=Ledger('workspace','working_agreements').read(access,'caller',workspace,request,lambda r:r['scope']=='workspace')
    assert result['complete']


def test_foreground_driver_uses_real_application_and_hides_labels():
    from scripts.memory_wiki_runtime_evaluation import foreground_case
    from scripts.memory_wiki_evaluation import load_suite
    from naiyue_test_agent.settings import AppSettings
    case=next(c for c in load_suite()[0] if c['case_id']=='W01')
    model=StreamingToolModel(replies=[AIMessage(content='没有需要保存的内容。')])
    result=asyncio.run(foreground_case(case,ModelCatalog('A',{'A':model}),'A'))
    assert result['status']=='completed' and result['observations']['answer']=='没有需要保存的内容。'
    assert 'allowed_changes' not in str(model.seen)
    assert 'Saved memory Wiki navigation' in str(model.seen)


def test_runtime_report_keeps_all_cases_after_setup_failure(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from scripts import memory_wiki_runtime_evaluation as runner
    from naiyue_test_agent import model_access,settings
    from naiyue_test_agent.memory_settings import MemorySettings
    catalog=ModelCatalog('A',{'A':StreamingToolModel(replies=[])})
    monkeypatch.setattr(model_access,'load_model_catalog',lambda _:catalog)
    monkeypatch.setattr(settings,'load_settings',lambda:SimpleNamespace(memory=MemorySettings()))
    async def execute(case,*args):
        if case['case_id']=='W01':raise OSError('injected setup failure')
        return dict(case_id=case['case_id'],status='completed',observations={},semantic_review=None)
    monkeypatch.setattr(runner,'background_case',execute)
    args=SimpleNamespace(config=None,model_id='A',split='calibration',mode='background',output_dir=tmp_path)
    result=asyncio.run(runner.run(args))
    assert len(result['cases'])==8 and result['cases'][0]['status']=='failed'
    assert result['cases'][-1]['status']=='completed'
    assert (tmp_path/'report.json').exists()


def test_foreground_driver_observes_real_write_and_independent_followup():
    from copy import deepcopy
    from scripts.memory_wiki_runtime_evaluation import foreground_case
    from scripts.memory_wiki_evaluation import load_suite
    from test_async_application import call
    from test_memory_wiki_mutations import args

    case = deepcopy(next(c for c in load_suite()[0] if c['case_id'] == 'W04'))
    case['input']['scenario']['followup'] = '开始工作前要核对什么？'
    source, = case['input']['sources']
    request = args(sources=[dict(current_user=True, quote=source['text'])])
    request['proposal']['body'] = '先跑相关回归，再汇报完成。'
    model = StreamingToolModel(replies=[
        call('memory', {'request': request}),
        AIMessage(content='已记住。'),
        call('memory', {'request': dict(action='search', scope='workspace', query='相关回归')}),
        AIMessage(content='先跑相关回归。'),
    ])
    result = asyncio.run(foreground_case(case, ModelCatalog('A', {'A': model}), 'A'))
    assert result['status'] == 'completed' and not model.replies
    observed = result['observations']
    assert observed['baseline_memories'] == []
    item, = observed['memories']
    assert item['body'] == request['proposal']['body']
    write, = observed['tool_feedback']
    assert write['status'] == 'success' and json.loads(write['content'])['item']['memory_id'] == item['memory_id']
    followup = observed['followup']
    assert followup['status'] == 'completed' and followup['answer'] == '先跑相关回归。'
    read, = followup['tool_feedback']
    assert read['status'] == 'success' and json.loads(read['content'])['items'][0]['memory_id'] == item['memory_id']
    assert source['text'] not in str(model.seen[2])


def test_foreground_cases_share_live_catalog_until_batch_owner_closes():
    from scripts.memory_wiki_runtime_evaluation import foreground_case
    from scripts.memory_wiki_evaluation import load_suite
    from http_observation_helpers import sdk_catalog, sdk_response

    async def run():
        cases = {c['case_id']: c for c in load_suite()[0]}
        calls = []

        def serve(request):
            calls.append(request.url.path)
            return sdk_response(request, 'openai')

        async with sdk_catalog('openai', serve=serve) as catalog:
            first = await foreground_case(cases['W01'], catalog, 'A')
            second = await foreground_case(cases['W04'], catalog, 'A')
            assert first['status'] == second['status'] == 'completed'
            assert len(calls) == 2

    asyncio.run(run())


def test_foreground_failure_keeps_already_delivered_tool_result():
    from scripts.memory_wiki_runtime_evaluation import foreground_case
    from scripts.memory_wiki_evaluation import load_suite
    from test_async_application import call

    case = next(c for c in load_suite()[0] if c['case_id'] == 'W04')
    model = StreamingToolModel(replies=[
        call('memory', {'request': dict(action='list_topics')}, identity='look'),
        RuntimeError('PRIVATE_PROVIDER_DETAIL'),
    ])
    result = asyncio.run(foreground_case(case, ModelCatalog('A', {'A': model}), 'A'))
    assert result['status'] == 'failed'
    assert result['observations']['stage'] == 'foreground_turn'
    assert result['observations']['logical_calls'] == 2
    feedback, = result['observations']['tool_feedback']
    assert feedback['tool_call_id'] == 'look' and feedback['status'] == 'success'
    assert result['observations']['memories'] == []
    assert 'PRIVATE_PROVIDER_DETAIL' not in json.dumps(result)


def test_background_run_followup_reads_saved_wiki_in_another_session():
    from copy import deepcopy
    from scripts.memory_wiki_runtime_evaluation import background_case
    from scripts.memory_wiki_evaluation import load_suite
    from memory_wiki_helpers import proposed
    from test_memory_batches import result
    from test_async_application import call
    from test_memory_wiki_worker import finish, read

    case = deepcopy(next(c for c in load_suite()[0] if c['case_id'] == 'W04'))
    case['input']['scenario']['followup'] = '报告完成前要做什么？'
    model = StreamingToolModel(replies=[
        AIMessage(content=json.dumps(result(proposals=[
            proposed(body='报告完成前应先执行相关回归。')]))),
        read(dict(action='list_pages', topic='working_agreements')),
        finish(decisions=[dict(relation='add', proposal_ids=['P1'], destination='N1')],
               page_changes=[dict(ref='N1', topic='working_agreements', title='回归约定', description='')]),
        call('memory', {'request': dict(action='search', scope='workspace', query='相关回归')}),
        AIMessage(content='先执行相关回归。'),
    ])
    outcome = asyncio.run(background_case(case, ModelCatalog('A', {'A': model}), 'A'))
    observed = outcome['observations']
    assert outcome['status'] == 'completed' and observed['saved'] == observed['overview_saved'] == 1, observed.get('followup')
    assert observed['followup']['status'] == 'completed' and observed['followup']['logical_calls'] == 2
    assert observed['followup']['memory_unchanged'] and observed['followup']['answer'] == '先执行相关回归。'
    proof, = observed['followup']['tool_feedback']
    assert json.loads(proof['content'])['items'][0]['memory_id'] == observed['memories'][0]['memory_id']
    assert len(model.seen) == 5 and not model.replies
