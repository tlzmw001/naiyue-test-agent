"""Foreground evaluation uses production Wiki tools and reports failures by phase."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from naiyue_test_agent.model_access import ModelCatalog
from scripts.memory_wiki_evaluation import load_suite
from test_async_application import StreamingToolModel, call
from test_memory_space_time_evaluation import load
from test_memory_wiki_mutations import args


def wiki_case(case_id='W01', *, followup=True):
    case = deepcopy(next(row for row in load_suite()[0] if row['case_id'] == case_id))
    if followup:
        case['input']['scenario']['followup'] = '回答时采用什么要求？'
    return case


def run_case(case, model):
    from scripts.memory_wiki_runtime_evaluation import foreground_case
    return asyncio.run(foreground_case(case, ModelCatalog('A', {'A': model}), 'A'))


def record(case, *, quote=None):
    text = case['input']['sources'][0]['text']
    request = args(sources=[dict(current_user=True, quote=text if quote is None else quote)])
    request.update(scope='personal', scope_source_indexes=[0])
    request['proposal'].update(body=text)
    request['destination']['new_page']['topic'] = 'collaboration'
    return request


def test_foreground_uses_real_tool_and_three_calls():
    case = wiki_case()
    model = StreamingToolModel(replies=[
        call('memory', {'request': record(case)}), AIMessage(content='已记录'), AIMessage(content='先给例子')])
    result = run_case(case, model)
    assert result['status'] == 'completed' and not model.replies
    observed = result['observations']
    assert observed['logical_calls'] == 2 and observed['followup']['logical_calls'] == 1
    assert len(observed['memories']) == 1 and observed['tool_feedback'][0]['status'] == 'success'
    assert observed['followup']['memory_unchanged']
    assert 'Saved memory Wiki navigation' in str(model.seen[2])


def test_foreground_bad_source_is_feedback_without_write():
    case = wiki_case()
    model = StreamingToolModel(replies=[
        call('memory', {'request': record(case, quote='不在用户输入里的话')}),
        AIMessage(content='未能保存'), AIMessage(content='未知')])
    observed = run_case(case, model)['observations']
    assert observed['tool_feedback'][0]['status'] == 'error' and observed['memories'] == []
    assert observed['logical_calls'] == 2 and observed['followup']['logical_calls'] == 1


def test_foreground_more_than_three_rounds_completes():
    model = StreamingToolModel(replies=[
        *(call('memory', {'request': dict(action='list_topics')}, identity=identity)
          for identity in ('one', 'two', 'three')),
        AIMessage(content='已完成'), AIMessage(content='未知')])
    result = run_case(wiki_case(), model)
    observed = result['observations']
    assert result['status'] == 'completed' and observed['logical_calls'] == 4
    assert len(model.seen) == 5 and observed['memories'] == [] and observed['followup']['memory_unchanged']


def test_aborted_run_retains_successful_tool_feedback():
    model = StreamingToolModel(replies=[
        call('memory', {'request': dict(action='list_topics')}), RuntimeError('PRIVATE-PROVIDER-BODY')])
    result = run_case(wiki_case(), model)
    observed = result['observations']
    assert result['status'] == 'failed' and observed['tool_feedback'][0]['status'] == 'success'
    assert observed['tool_feedback'][0]['tool_call_id'] == 't' and observed['logical_calls'] == 2
    assert 'PRIVATE-PROVIDER-BODY' not in json.dumps(result)


def test_frontend_labels_keep_kind_change_as_rejection():
    """Frozen 143BM labels remain historical, independently of the Wiki protocol."""
    from scripts.memory_foreground_evaluation import foreground_labels
    case = load()[0][5]
    result = foreground_labels(case)
    assert result['lifecycle_expectation'] == 'direct_kind_change_rejected_old_unchanged'
    assert not any(f['must_activate'] for f in result['expected_facts'])
    assert result['background_expected_facts'] == case['expected_facts']


def test_removed_kind_is_rejected_and_old_identity_preserved():
    class ChangeModel(StreamingToolModel):
        async def _next(self, messages):
            if len(self.seen) == 1:
                result = next(m for m in reversed(messages) if m.type == 'tool')
                old, = json.loads(result.content)['items']
                user = next(m.content for m in reversed(messages) if m.type == 'human')
                request = dict(action='update', scope=old['scope'], memory_id=old['memory_id'],
                    expected_version=old['version'], change=dict(mode='replace', proposal=dict(
                        title='变更', subject='变更', kind='convention', body=user, basis='user_declaration',
                        intent='activate', reason='当前约定')), sources=[dict(current_user=True, quote=user)])
                self.replies.insert(0, call('memory', {'request': request}, identity='forbidden'))
            return await super()._next(messages)

    model = ChangeModel(replies=[call('memory', {'request': dict(action='search', scope='workspace',
        query='隔离 Python 环境')}, identity='search'), AIMessage(content='改类被拒绝'), AIMessage(content='仍是原记录')])
    observed = run_case(wiki_case('W07'), model)['observations']
    assert [row['status'] for row in observed['tool_feedback']] == ['success', 'error']
    assert observed['memories'] == observed['baseline_memories']
    assert observed['memories'][0]['version'] == 1
    assert observed['logical_calls'] == 3 and observed['followup']['logical_calls'] == 1


def batch_args(tmp_path, split='calibration'):
    return SimpleNamespace(config=None, output_dir=tmp_path, model_id='A', split=split, mode='foreground')


def fake_catalog(monkeypatch, replies, *, close=None):
    from naiyue_test_agent import model_access, settings
    from naiyue_test_agent.memory_settings import MemorySettings

    model = StreamingToolModel(replies=list(replies))
    catalog = ModelCatalog('A', {'A': model})
    if close is not None:
        catalog.aclose = close
    monkeypatch.setattr(model_access, 'load_model_catalog', lambda _: catalog)
    monkeypatch.setattr(settings, 'load_settings', lambda: SimpleNamespace(memory=MemorySettings()))
    return model


def test_cli_batch_runs_all_calibration_cases_and_retains_identity(monkeypatch, tmp_path):
    from scripts.memory_wiki_runtime_evaluation import run

    model = fake_catalog(monkeypatch, [AIMessage(content='不作修改') for _ in range(7)])
    report = asyncio.run(run(batch_args(tmp_path)))
    assert len(report['cases']) == 8 and report['model_requests'] == 7 and not model.replies
    assert [row['status'] for row in report['cases']].count('completed') == 6
    assert report['mode'] == 'foreground' and report['semantic_status'] == 'needs_review'
    assert report['provenance']['source_sha256'] and (tmp_path / 'source.zip').exists()


def test_unbounded_calls_keep_distinct_feedback_and_write():
    case = wiki_case()
    model = StreamingToolModel(replies=[
        call('memory', {'request': dict(action='list_topics')}, identity='first'),
        call('memory', {'request': record(case)}, identity='second'),
        call('memory', {'request': dict(action='list_topics')}, identity='third'),
        AIMessage(content='已保存'), AIMessage(content='先给例子')])
    result = run_case(case, model)
    observed = result['observations']
    assert {f['tool_call_id'] for f in observed['tool_feedback']} == {'first','second','third'}
    assert observed['logical_calls'] == 4 and result['status'] == 'completed'
    assert len(observed['memories']) == 1 and observed['followup']['memory_unchanged']


def test_followup_prepare_failure_keeps_case_evidence(monkeypatch):
    from scripts import memory_wiki_runtime_evaluation as module
    original = module._foreground_turn

    async def fail_second(app, sid, prompt, model, observed):
        if '不修改任何记忆' in prompt:
            raise OSError('PRIVATE')
        return await original(app, sid, prompt, model, observed)

    monkeypatch.setattr(module, '_foreground_turn', fail_second)
    result = run_case(wiki_case(), StreamingToolModel(replies=[AIMessage(content='done')]))
    observed = result['observations']
    assert result['status'] == 'failed' and observed['logical_calls'] == 1
    assert observed['memories'] == [] and observed['answer'] == 'done'
    assert observed['stage'] == 'followup' and observed['followup']['status'] == 'failed'
    assert 'PRIVATE' not in json.dumps(result)


def test_frontend_change_labels_do_not_require_new_fact():
    """Legacy direct-write labels must not repurpose old background expectations."""
    from scripts.memory_foreground_evaluation import foreground_labels
    case = load()[0][5]
    labels = foreground_labels(case)
    assert not any(f['must_extract'] for f in labels['expected_facts'] if f.get('target_old'))
    assert labels['answer_requirements'] != case['answer_requirements']
    assert labels['background_answer_requirements'] == case['answer_requirements']
    assert labels['foreground_labels_sha256']


def test_refresh_is_denied_without_background():
    model = StreamingToolModel(replies=[call('memory', {'request': dict(action='refresh_overview')}),
                                          AIMessage(content='denied')])
    observed = run_case(wiki_case(followup=False), model)['observations']
    feedback, = observed['tool_feedback']
    assert feedback['status'] == 'error'
    assert observed['logical_calls'] == 2 and observed['memories'] == []


def test_catalog_cleanup_failure_retains_results_and_batch(monkeypatch, tmp_path):
    from scripts.memory_wiki_runtime_evaluation import run
    closed = []

    async def fail():
        closed.append(True)
        raise OSError('PRIVATE-CLEANUP')

    fake_catalog(monkeypatch, [AIMessage(content='done') for _ in range(7)], close=fail)
    report = asyncio.run(run(batch_args(tmp_path)))
    assert closed == [True] and len(report['cases']) == 8
    assert report['model_requests'] == 7 and report['cleanup_error'] == 'OSError'
    assert 'PRIVATE-CLEANUP' not in (tmp_path / 'report.json').read_text()
    assert len([row for row in report['cases'] if row['status'] == 'completed']) == 6


def test_followup_real_model_and_snapshot_failures_preserve_requests(monkeypatch):
    from scripts import memory_wiki_runtime_evaluation as runner

    error = RuntimeError('SECRET_PROVIDER_FAILURE')
    model = StreamingToolModel(replies=[AIMessage(content='done'), error])
    result = run_case(wiki_case(), model)
    observed = result['observations']
    assert result['status'] == 'failed' and observed['stage'] == 'followup'
    assert observed['answer'] == 'done' and observed['followup']['logical_calls'] == 1
    assert observed['followup']['status'] == 'failed' and observed['memories'] == []
    assert 'SECRET_PROVIDER_FAILURE' not in json.dumps(result)

    original = runner._memory_snapshot
    count = 0

    def fail_final(access):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError('SECRET_SNAPSHOT_FAILURE')
        return original(access)

    monkeypatch.setattr(runner, '_memory_snapshot', fail_final)
    model = StreamingToolModel(replies=[AIMessage(content='done'), AIMessage(content='answer')])
    result = run_case(wiki_case(), model)
    observed = result['observations']
    assert result['status'] == 'failed' and observed['stage'] == 'followup'
    assert observed['followup']['answer'] == 'answer' and observed['followup']['logical_calls'] == 1
    assert observed['followup']['status'] == 'failed' and 'SECRET_SNAPSHOT_FAILURE' not in json.dumps(result)


def test_search_record_final_followup_uses_four_calls():
    case = wiki_case()
    model = StreamingToolModel(replies=[
        call('memory', {'request': dict(action='search', scope='personal', query='讲解')}, identity='search'),
        call('memory', {'request': record(case)}, identity='record'),
        AIMessage(content='已保存'), AIMessage(content='先给例子')])
    result = run_case(case, model)
    observed = result['observations']
    assert result['status'] == 'completed' and observed['logical_calls'] == 3
    assert observed['followup']['logical_calls'] == 1 and observed['followup']['memory_unchanged']
    assert len(observed['memories']) == 1
    assert [r['status'] for r in observed['tool_feedback']] == ['success', 'success']


def test_catalog_preflight_failure_releases_batch_client_once(monkeypatch, tmp_path):
    import pytest
    from scripts.memory_wiki_runtime_evaluation import run

    model = fake_catalog(monkeypatch, [])
    from naiyue_test_agent import model_access
    catalog = model_access.load_model_catalog(None)
    closed = []

    def prepare():
        raise RuntimeError('preflight error')

    async def close():
        closed.append(True)

    monkeypatch.setattr(catalog, 'prepare_async_clients', prepare)
    monkeypatch.setattr(catalog, 'aclose', close)
    with pytest.raises(RuntimeError, match='preflight error'):
        asyncio.run(run(batch_args(tmp_path)))
    assert closed == [True] and not model.seen
    assert not (tmp_path / 'report.json').exists()
