"""Verify evaluation separation and real-result metrics, not model quality."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from docs.research.memory00 import benchmark


HERE = Path(__file__).resolve().parent


def inputs():
    return json.loads((HERE / 'fixtures.json').read_text()), json.loads((HERE / 'protocol.json').read_text())


def test_calibration_does_not_use_holdout_labels():
    data, protocol = inputs()
    altered = deepcopy(data)
    for query in altered['queries']:
        if query['split'] == 'holdout':
            query['relevant_ids'], query['required_ids'] = ['m01'], ['m01']
            query['no_answer'] = False
    before = benchmark.calibrate(data, protocol)
    after = benchmark.calibrate(altered, protocol)
    assert set(before) == {'substring', 'unicode61', 'adapted'}
    assert {key: value['weights'] for key, value in before.items()} == {
        key: value['weights'] for key, value in after.items()}
    assert all(len(value['queries']) == 12 for value in before.values())


def test_metrics_count_actual_omissions_and_forbidden_results():
    query = {'id': 'probe', 'relevant_ids': ['a', 'b'], 'required_ids': ['a'],
             'forbidden_ids': ['secret']}
    result = benchmark.measure(query, [{'id': 'b'}, {'id': 'secret'}], limit=8)
    assert result['recall_at_8'] == 0.5
    assert result['precision_returned_at_8'] == 0.5
    assert result['required_hits'] == 0
    assert result['forbidden_hits'] == ['secret']
    assert result['actual_ids'] == ['b', 'secret']


def test_protocol_rejects_modified_fixtures_before_evaluation():
    with pytest.raises(ValueError, match='fixture hash'):
        benchmark.check_fixture_hash(b'changed', {'fixtures_sha256': 'not-this-hash'})
