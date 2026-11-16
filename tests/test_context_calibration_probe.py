"""Probe reports preserve prior predictions and reject incomplete evidence."""
import asyncio
import json
import pytest
from scripts.probe_context_calibration import run_probe
from scripts.context_calibration_report import Measurements, metrics


def test_paired_summary_and_agent_deduplicate_without_recomputing_prediction():
    rows = Measurements()
    rows.summary({'event': 'context_summary_model_started', 'sample_id': 'one',
        'raw_input_tokens': 100, 'calibrated_input_tokens': 400, 'method': 'ratio_estimate'})
    rows.summary({'event': 'context_summary_model_finished', 'sample_id': 'one', 'input_tokens': 350})
    rows.summary({'event': 'context_summary_model_finished', 'sample_id': 'one', 'input_tokens': 350})
    assert len(rows.rows) == 1
    assert rows.rows[0]['C'] == 400 and rows.rows[0]['A'] == 350
    assert metrics(rows.rows)['calibrated_count'] == 0


def test_same_calibrated_denominator_excludes_cold_missing_partial_summary():
    rows = [{'purpose': 'agent', 'status': 'returned', 'method': 'ratio_estimate', 'E': 100,
             'C': 400, 'A': 380} for _ in range(3)]
    rows += [{'purpose': 'agent', 'status': 'returned', 'method': 'uncalibrated', 'E': 1, 'C': 1, 'A': 900},
             {'purpose': 'agent', 'status': 'failed', 'method': 'ratio_estimate', 'E': 1, 'C': 4, 'A': None}]
    result = metrics(rows)
    assert result['calibrated_count'] == 3 and result['improvement'] is True
    assert result['excluded_count'] == 2
    assert metrics(rows[:2])['improvement'] is False


def test_preflight_without_live_never_loads_clients_or_overwrites_report(tmp_path, monkeypatch):
    import scripts.probe_context_calibration as probe
    monkeypatch.setattr(probe, 'load_model_catalog', lambda _: pytest.fail('preflight performed IO'))
    path = tmp_path / 'report.json'
    assert asyncio.run(run_probe('missing', 'A', path)) == 2
    assert json.loads(path.read_text())['http']['used'] == 0
    with pytest.raises(FileExistsError):
        asyncio.run(run_probe('missing', 'A', path))


@pytest.mark.parametrize('limit', [0, 13, True])
def test_invalid_limit_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        asyncio.run(run_probe('missing', 'A', tmp_path / 'report', request_limit=limit))


def test_real_snapshot_envelope_and_unpaired_summary_are_not_false_success():
    from types import SimpleNamespace
    rows = Measurements()
    payload = {'status': 'returned', 'model_call_id': 'one', 'estimate': {
        'estimated_input_tokens': 100, 'calibration': {'calibrated_input_tokens': 400, 'method': 'ratio_estimate'}},
        'usage': {'input_tokens': 380}}
    event = SimpleNamespace(event_type='model_context_snapshot', sequence=3, data=SimpleNamespace(snapshot_json=json.dumps(payload)))
    rows.event(event)
    rows.event(event)
    rows.summary({'event': 'context_summary_model_finished', 'sample_id': 'missing', 'input_tokens': 20})
    assert len(rows.rows) == 2 and rows.rows[0]['C'] == 400
    assert rows.rows[1]['status'] == 'incomplete'


def test_full_plan_uses_real_runtime_and_persists_numeric_report(tmp_path, monkeypatch):
    from test_context_calibration_runtime import identified
    from test_context_inspector import model_with_limits
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.model_access import ModelCatalog
    import scripts.probe_context_calibration as probe
    def load(_):
        model = identified(model_with_limits([AIMessage(content='ACK', usage_metadata={
            'input_tokens': 2000, 'output_tokens': 1, 'total_tokens': 2001}) for _ in range(4)]))
        return ModelCatalog('A', {'A': model})
    monkeypatch.setattr(probe, 'load_model_catalog', load)
    monkeypatch.setattr(probe, 'guard_catalog', lambda *args: None)
    # Verify the formal application path before live: stop after C2, not a parallel agent loop.
    original = probe.run_turn
    async def turn(app, sid, prompt):
        if 'Read calibration.txt' in prompt:
            raise probe.NotCovered()
        await original(app, sid, prompt)
    monkeypatch.setattr(probe, 'run_turn', turn)
    path = tmp_path / 'report.json'
    assert asyncio.run(probe.run_probe('unused', 'A', path, live=True)) == 3
    value = json.loads(path.read_text())
    assert [r['method'] for r in value['measurements']] == ['uncalibrated', 'ratio_estimate']
    assert value['measurements'][0]['A'] == value['measurements'][1]['A'] == 2000
    assert value['scenarios'][3]['status'] == 'not_run'


@pytest.mark.parametrize('defect', ['none', 'wrong_run', 'failed_recovery', 'duplicate_failed', 'missing_retry'])
def test_only_correlated_successful_overflow_attempt_is_exempt(defect):
    rows = Measurements()
    failed = dict(id='call', run_id='run', purpose='agent', status='failed', E=100, C=100, A=None)
    retry = dict(id='call', run_id='run', purpose='agent', status='returned', E=50, C=50, A=60)
    rows.rows = [failed, retry]
    rows.operations = [dict(event='context_recovery_finished', model_call_id='call', run_id='run', status='recovered', retry_attempts=1)]
    if defect == 'wrong_run':
        rows.operations[0]['run_id'] = 'another'
    elif defect == 'failed_recovery':
        rows.operations[0]['status'] = 'failed'
    elif defect == 'duplicate_failed':
        rows.rows.insert(0, dict(failed))
    elif defect == 'missing_retry':
        rows.rows.pop()
    assert rows.recovered_overflow(failed) is (defect == 'none')
    assert not rows.recovered_overflow(retry)
