import pytest


@pytest.mark.parametrize('split', ['calibration','validation','holdout'])
def test_worker_matrix_uses_complete_split_and_real_side_effects(split):
    import asyncio
    from langchain_core.messages import AIMessage
    from scripts.memory_wiki_evaluation import load_suite
    from scripts.memory_wiki_runtime_evaluation import background_case
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import StreamingToolModel
    from test_memory_batches import result
    import json

    async def run():
        cases = [row for row in load_suite()[0] if row['split'] == split]
        assert len(cases) == 8
        for case in cases:
            source = case['input']
            followup = bool(source['scenario'].get('followup') or source['scenario'].get('navigation_target'))
            replies = [AIMessage(content=json.dumps(result()))]
            if followup:
                replies.append(AIMessage(content='独立答复'))
            model = StreamingToolModel(replies=replies)
            observed = await background_case(case, ModelCatalog('A', {'A': model}), 'A')
            assert observed['case_id'] == case['case_id'] and not model.replies
            assert observed['status'] == 'completed', observed
            facts = observed['observations']
            assert facts['saved'] == 0 and facts['overview_saved'] == 1
            assert len(facts['memories']) == len(source['wiki']['memories'])
            assert len(model.seen) == (2 if followup else 1)
            if followup:
                assert facts['followup']['memory_unchanged']

    asyncio.run(run())


@pytest.mark.parametrize('split', ['calibration','validation','holdout'])
def test_recall_matrix_uses_complete_split_and_readonly_exact_sets(split, tmp_path):
    import time
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.storage.project_store import ProjectStore
    from scripts.memory_wiki_evaluation import load_suite
    from scripts.memory_wiki_runtime_evaluation import seed_wiki
    from test_memory_wiki_pagination import decode_content

    rows = [row for row in load_suite()[0] if row['split'] == split]
    assert len(rows) == 8
    for case in rows:
        root = tmp_path / case['case_id']
        root.mkdir()
        with ProjectStore(root / 'state.sqlite') as store:
            project = '11111111-1111-4111-8111-111111111111'
            now = time.time()
            store.sessions.bind_workspace(project, root)
            store.sessions.create('old', dict(thread_id='old', model_id='A', workspace_root=root,
                active_tool_names=['memory'], permission_mode='Default'))
            store.memory.prepare_wiki(now=now)
            access = MemoryAccess(store, MemorySettings(), root / 'data')
            try:
                source = case['input']
                pages = seed_wiki(access, 'old', source, now)
                stores = dict(access._stores('both'))
                changes = {scope: repo.database.connection.total_changes for scope, repo in stores.items()}
                for scope in stores:
                    for topic in ('working_agreements', 'collaboration', 'stable_knowledge'):
                        expected = {pages[item['page_id']][1]['page_id'] for item in source['wiki']['memories']
                            if item['status'] == 'active' and item['expires_at'] > source['now']
                            and pages[item['page_id']][0] == scope
                            and pages[item['page_id']][1]['topic'] == topic}
                        cursor = None
                        seen = []
                        for _ in range(100):
                            request = dict(action='list_pages', scope=scope, topic=topic, cursor=cursor)
                            value = access.query_wiki('old', project, request, now=now)
                            seen.extend(row['page_id'] for row in decode_content(value)['pages'])
                            if not value['has_more']:
                                break
                            assert value['next_cursor'] and value['next_cursor'] != cursor
                            cursor = value['next_cursor']
                        assert len(seen) == len(expected) and set(seen) == expected
                assert {scope: repo.database.connection.total_changes for scope, repo in stores.items()} == changes
            finally:
                access.close()


def test_bad_split_rejected_before_evaluation():
    from scripts.memory_wiki_evaluation import plan
    with pytest.raises(ValueError, match='split_invalid'):
        plan('all')


def test_coverage_accounts_for_every_case_without_relabeling_semantic_success():
    from scripts.memory_wiki_evaluation import load_suite, plan, validate_report

    cases, protocol = load_suite()
    assert {tag for case in cases for tag in case['coverage']} == set(protocol['required_coverage'])
    for split in protocol['splits']:
        planned = plan(split)
        report = dict(planned, cases=[dict(case_id=case['case_id'], status='skipped',
            observations=dict(reason='not_executed'), semantic_review=None) for case in planned['inputs']])
        checked = validate_report(report)
        assert checked['expected_cases'] == checked['skipped'] == 8
        assert checked['completed'] == 0 and checked['product_quality_passed'] is None


@pytest.mark.parametrize('fault',['hash','family'])
def test_matrix_preflight_rejects_modified_or_leaking_assets(monkeypatch,tmp_path,fault):
    import hashlib,json
    from scripts import memory_evaluation_cases as module
    original=module.FIXTURES.parent
    for suffix in ('.json','_protocol.json'):
        name='worker_engineering_v1'+suffix
        (tmp_path/name).write_bytes((original/name).read_bytes())
    fixture=tmp_path/'worker_engineering_v1.json';data=json.loads(fixture.read_text())
    data['cases'][8]['family_id']=data['cases'][0]['family_id']
    fixture.write_text(json.dumps(data))
    if fault=='family':
        protocol=tmp_path/'worker_engineering_v1_protocol.json';values=json.loads(protocol.read_text())
        values['fixtures_sha256']=hashlib.sha256(fixture.read_bytes()).hexdigest();protocol.write_text(json.dumps(values))
    monkeypatch.setattr(module,'FIXTURES',tmp_path/'evidence.json')
    with pytest.raises(ValueError):module.load_engineering_matrix('worker','calibration')
