"""Offline research behavior tests; these do not certify product memory."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('memory00_retrieval', HERE / 'retrieval.py')
retrieval = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = retrieval
spec.loader.exec_module(retrieval)

CONTEXT = {'workspace_id': 'ws-a', 'machine': 'macos', 'target': 'staging'}
NOW = '2026-10-22T12:00:00Z'


def record(identity='eligible', **changes):
    return {'id': identity, 'scope': 'workspace', 'workspace_id': 'ws-a',
        'kind': 'context', 'status': 'active', 'title': '代理配置',
        'body': '请求路径需要核对', 'aliases': [], **changes}


def identities(result):
    return [row['id'] for row in result]


def test_fixture_inventory_and_source_labels_are_real_inputs():
    data = json.loads((HERE / 'fixtures.json').read_text())
    assert data['synthetic'] is True
    assert len(data['queries']) == 24
    assert sum(q['split'] == 'calibration' for q in data['queries']) == 12
    assert sum(q['split'] == 'holdout' for q in data['queries']) == 12
    assert len(data['extraction_cases']) == 12
    assert len(data['workflows']) == 4
    known = {m['id'] for m in data['memories']}
    assert len(known) == len(data['memories'])
    for query in data['queries']:
        assert set(query['required_ids']) <= set(query['relevant_ids']) <= known
        assert set(query.get('forbidden_ids', [])) <= known
    for case in data['extraction_cases']:
        sources = {s['id'] for s in case['sources']}
        assert set(case['expected']['required_sources']) <= sources


def test_chinese_two_character_query_returns_original_not_index_tokens():
    with retrieval.ResearchIndex([record()], method='adapted') as index:
        found = index.search('代理', CONTEXT, NOW)
    assert identities(found) == ['eligible']
    assert found[0]['title'] == '代理配置'
    assert found[0]['body'] == '请求路径需要核对'


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
@pytest.mark.parametrize(('term', 'other'), [
    ('NO_PROXY', 'NO_PROXY_EXTRA'), ('TC-01', 'TC-010'), ('0.5', '0'),
    ('0', '0.5'), ('tests/api/test_order.py', 'tests/api/test_order_extra.py'),
    ('cvr_ltv', 'cvr_ltv_extra'),
])
def test_exact_identifiers_do_not_accept_near_names(method, term, other):
    rows = [record('right', title=term), record('wrong', title=other)]
    with retrieval.ResearchIndex(rows, method=method) as index:
        assert identities(index.search(term, CONTEXT, NOW)) == ['right']


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
def test_filters_happen_before_top_k(method):
    rows = [record('good', title='代理', aliases=['代理'])]
    conditions = [dict(workspace_id='ws-b'), dict(status='candidate'),
        dict(status='inactive'), dict(forgotten=True), dict(machine='windows'),
        dict(target='production'), dict(expires_at='2026-10-20T00:00:00Z')]
    rows += [record(f'bad-{i}', title='代理', aliases=['代理'], **conditions[i % 7])
        for i in range(70)]
    with retrieval.ResearchIndex(rows, method=method) as index:
        assert identities(index.search('代理', CONTEXT, NOW, limit=1)) == ['good']


def test_project_and_personal_are_ranked_in_independent_corpora():
    rows = [record('project', title='代理', aliases=['代理']),
        record('personal', scope='personal', workspace_id=None, title='代理', aliases=['代理'])]
    rows += [record(f'noise-{i}', title='代理背景', body='代理背景' * 30) for i in range(50)]
    with retrieval.ResearchIndex(rows, method='adapted') as index:
        found = index.search('代理', CONTEXT, NOW, limit=2)
    assert set(identities(found)) == {'project', 'personal'}
    assert found[0]['score'] == found[1]['score']


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
def test_stale_index_is_not_authoritative_after_forgetting(method):
    with retrieval.ResearchIndex([record(title='NO_PROXY')], method=method) as index:
        db = index.connections['workspace']
        db.execute("UPDATE memories SET forgotten=1, body='', title='' WHERE id=?", ('eligible',))
        assert index.search('NO_PROXY', CONTEXT, NOW) == []


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
@pytest.mark.parametrize('advance_version', [False, True])
def test_stale_index_cannot_recall_corrected_unrelated_text(method, advance_version):
    with retrieval.ResearchIndex([record()], method=method) as index:
        db = index.connections['workspace']
        db.execute('UPDATE memories SET title=?,body=? WHERE id=?',
                   ('发布流程', '构建与安装检查', 'eligible'))
        if advance_version:
            db.execute('UPDATE memories SET version=version+1 WHERE id=?', ('eligible',))
        assert index.search('代理配置', CONTEXT, NOW) == []


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
def test_correction_after_ranking_invalidates_the_old_hit(method, monkeypatch):
    with retrieval.ResearchIndex([record()], method=method) as index:
        rank = index._rank

        def correct_after_rank(connection, text, parameters):
            found = rank(connection, text, parameters)
            if connection is index.connections['workspace']:
                connection.execute('UPDATE memories SET title=?,body=? WHERE id=?',
                                   ('发布流程', '构建与安装检查', 'eligible'))
            return found

        monkeypatch.setattr(index, '_rank', correct_after_rank)
        assert index.search('代理配置', CONTEXT, NOW) == []


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
def test_expansion_technical_anchor_rejects_near_name(method):
    with retrieval.ResearchIndex([record(title='NO_PROXY_EXTRA')], method=method) as index:
        assert index.search('代理', CONTEXT, NOW, expansions=['NO_PROXY']) == []


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
def test_expansion_preserves_original_technical_anchor(method):
    with retrieval.ResearchIndex([record(title='代理 TC-02')], method=method) as index:
        assert index.search('TC-01', CONTEXT, NOW, expansions=['代理 TC-02']) == []


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
def test_query_operators_are_data_and_cannot_mutate_database(method):
    with retrieval.ResearchIndex([record(title='NO_PROXY')], method=method) as index:
        assert index.search('" OR * ; DROP TABLE memories; --', CONTEXT, NOW) == []
        assert index.search('', CONTEXT, NOW) == []
        assert index.search('月球基地', CONTEXT, NOW) == []
        assert index.connections['workspace'].execute('SELECT count(*) FROM memories').fetchone()[0] == 1


def test_unknown_machine_is_visible_as_unverified_not_assumed_matching():
    with retrieval.ResearchIndex([record(machine='windows')], method='adapted') as index:
        found = index.search('代理', {**CONTEXT, 'machine': None}, NOW)
    assert identities(found) == ['eligible']
    assert found[0]['needs_verification'] is True


def test_query_expansions_merge_and_deduplicate_actual_results():
    with retrieval.ResearchIndex([record(title='代理', aliases=['proxy'])], method='adapted') as index:
        found = index.search('代理', CONTEXT, NOW, expansions=['proxy', '代理'])
    assert identities(found) == ['eligible']


@pytest.mark.parametrize('method', ['substring', 'unicode61', 'adapted'])
def test_explicit_module_mismatch_is_filtered_before_limit(method):
    rows = [record('orders', title='代理', aliases=['代理'], module='orders'),
            record('billing', title='代理', aliases=['代理'], module='billing')]
    with retrieval.ResearchIndex(rows, method=method) as index:
        found = index.search('代理', {**CONTEXT, 'module': 'orders'}, NOW, limit=1)
    assert identities(found) == ['orders']


@pytest.mark.parametrize('limit', [0, -1, 51, True])
def test_invalid_result_budget_is_rejected(limit):
    with retrieval.ResearchIndex([record()], method='adapted') as index:
        with pytest.raises(ValueError, match='limit'):
            index.search('代理', CONTEXT, NOW, limit=limit)
