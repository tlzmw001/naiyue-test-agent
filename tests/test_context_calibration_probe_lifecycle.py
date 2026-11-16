"""Probe orchestration contracts, complementing real-runtime calibration tests."""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import scripts.probe_context_calibration as probe


def controlled_application(monkeypatch, *, fail=None, missing_usage=False, lose_samples=False):
    state = SimpleNamespace(messages=[], order=[], callback=None, closed=0, sends=0)

    class Catalog:
        def get(self, model_id):
            assert model_id == 'fixture'
            return SimpleNamespace(metadata={})

        async def aclose(self):
            state.closed += 1

    monkeypatch.setattr(probe, 'load_model_catalog', lambda _: Catalog())
    def guard(catalog, budget):
        state.budget = budget
    monkeypatch.setattr(probe, 'guard_catalog', guard)

    async def create(model_id, work):
        assert model_id == 'fixture' and (work / 'calibration.txt').exists()
        return 'session'

    async def select(sid, names):
        assert sid == 'session' and names == ['read']

    async def restore(sid):
        return sid

    async def messages(sid):
        return list(state.messages)

    async def compact(sid):
        state.order.append('C4')
        state.budget.reserve('fixture')
        state.sends += 1
        handler = next(h for h in probe.logging.getLogger('naiyue_test_agent.agent_runtime.context_compaction').handlers if isinstance(h, probe.Measurements))
        handler.summary(dict(event='context_summary_model_started', sample_id='summary', raw_input_tokens=100, calibrated_input_tokens=100 if missing_usage else 380, method='uncalibrated' if missing_usage else 'inherited_agent_ratio'))
        handler.summary(dict(event='context_summary_model_finished', sample_id='summary', input_tokens=None if missing_usage else 50))
        state.messages[:] = [AIMessage(content=probe.FACT)]
        return {'compaction_status': 'applied'}

    def subscribe(callback):
        state.callback = callback
        return lambda: setattr(state, 'callback', None)

    @asynccontextmanager
    async def opened(config, **options):
        assert options['response_format'] is None
        assert options['initial_active_tools'] == []
        assert [spec.tool.name for spec in options['tools']] == ['read']
        yield SimpleNamespace(host=SimpleNamespace(
            sessions=SimpleNamespace(create_workspace=create, select_tools=select, restore=restore),
            history=SimpleNamespace(messages=messages), runs=SimpleNamespace(compact=compact),
            observability=SimpleNamespace(subscribe=subscribe)))

    async def turn(app, sid, prompt):
        name = ('C1', 'C2', 'C3', 'C5', 'C6')[sum(n != 'C4' for n in state.order)]
        state.order.append(name)
        if name == fail:
            raise RuntimeError('SENSITIVE_PROVIDER_TOKEN_SENTINEL')
        for _ in range(2 if name == 'C3' else 1):
            state.budget.reserve('fixture')
            state.sends += 1
            snapshot = {'model_call_id': str(state.sends), 'status': 'returned',
                'estimate': {'estimated_input_tokens': 100,
                    'calibration': {'calibrated_input_tokens': 100 if name == 'C1' or (lose_samples and name in ('C5', 'C6')) else 380,
                                    'method': 'uncalibrated' if name == 'C1' or (lose_samples and name in ('C5', 'C6')) else 'ratio_estimate'}},
                'usage': None if missing_usage else {'input_tokens': 380}}
            state.callback(SimpleNamespace(event_type='model_context_snapshot', sequence=state.sends,
                data=SimpleNamespace(snapshot_json=json.dumps(snapshot))))
        if name == 'C3':
            state.messages.extend([AIMessage(content='', tool_calls=[
                {'id': 'read', 'name': 'read', 'args': {'path': 'calibration.txt'}}]),
                ToolMessage(content='synthetic', name='read', tool_call_id='read', status='success')])
        state.messages.append(AIMessage(content='ACK'))

    monkeypatch.setattr(probe, 'open_component_application', opened)
    monkeypatch.setattr(probe, 'run_turn', turn)
    return state


def test_all_six_scenarios_finish_in_order_and_keep_one_ledger(monkeypatch, tmp_path):
    state = controlled_application(monkeypatch)
    path = tmp_path / 'report.json'
    assert asyncio.run(probe.run_probe('unused', 'fixture', path, live=True)) == 0
    report = json.loads(path.read_text())
    assert state.order == list(probe.SCENARIOS)
    assert [s['status'] for s in report['scenarios']] == ['completed'] * 6
    assert report['status'] == 'completed'
    assert report['http']['used'] == state.sends == 7
    assert state.closed == 3
    assert report['quality_checks']['fixed_fact_retained'] is True


def test_failure_stops_following_scenarios_and_never_serializes_error(monkeypatch, tmp_path):
    state = controlled_application(monkeypatch, fail='C2')
    path = tmp_path / 'report.json'
    assert asyncio.run(probe.run_probe('unused', 'fixture', path, live=True)) == 1
    text = path.read_text()
    report = json.loads(text)
    assert 'SENSITIVE_PROVIDER_TOKEN_SENTINEL' not in text
    assert [r['status'] for r in report['scenarios']] == ['completed', 'failed'] + ['not_run'] * 4
    assert state.sends == 1 and report['status'] == 'failed'


def test_missing_usage_returns_incomplete_after_bounded_plan(monkeypatch, tmp_path):
    state = controlled_application(monkeypatch, missing_usage=True)
    path = tmp_path / 'report.json'
    assert asyncio.run(probe.run_probe('unused', 'fixture', path, live=True)) == 3
    report = json.loads(path.read_text())
    assert report['status'] == 'incomplete' and report['reason'] == 'usage_insufficient'
    assert report['metrics']['calibrated_count'] == 0
    assert state.sends == 7


def test_cancellation_is_recorded_and_propagated(monkeypatch, tmp_path):
    controlled_application(monkeypatch)
    async def cancel(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(probe, 'execute', cancel)
    path = tmp_path / 'report.json'
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(probe.run_probe('unused', 'fixture', path, live=True))
    report = json.loads(path.read_text())
    assert report['status'] == report['reason'] == 'cancelled'
    assert report['http']['used'] == 0 and report['ended']


def test_report_save_failure_stops_before_another_request(monkeypatch, tmp_path):
    state = controlled_application(monkeypatch)
    calls = []
    def fsync(descriptor):
        calls.append(descriptor)
        if len(calls) >= 3:
            raise OSError('SENSITIVE_DISK_ERROR_SENTINEL')
    monkeypatch.setattr(probe.os, 'fsync', fsync)
    with pytest.raises(OSError):
        asyncio.run(probe.run_probe('unused', 'fixture', tmp_path / 'report.json', live=True))
    assert state.sends == 1
    assert state.order == ['C1']


def test_existing_report_is_never_overwritten_or_sent(monkeypatch, tmp_path):
    state = controlled_application(monkeypatch)
    path = tmp_path / 'report.json'
    path.write_text('previous-evidence')
    with pytest.raises(FileExistsError):
        asyncio.run(probe.run_probe('unused', 'fixture', path, live=True))
    assert path.read_text() == 'previous-evidence' and state.sends == 0


def test_lost_compaction_or_reopen_samples_cannot_pass(monkeypatch, tmp_path):
    controlled_application(monkeypatch, lose_samples=True)
    path = tmp_path / 'report.json'
    assert asyncio.run(probe.run_probe('unused', 'fixture', path, live=True)) == 1
    assert json.loads(path.read_text())['reason'] == 'calibration_contract_failed'

@pytest.mark.parametrize('defect', ['missing_tail', 'missing_summary', 'unpaired', 'wrong_ratio'])
def test_missing_or_invalid_evidence_cannot_pass(monkeypatch, tmp_path, defect):
    controlled_application(monkeypatch)
    original = probe.execute
    async def execute(*args):
        await original(*args)
        rows = args[4].rows
        if defect == 'missing_tail':
            rows[:] = [r for r in rows if r['scenario'] not in ('C5', 'C6')]
        elif defect == 'missing_summary':
            rows[:] = [r for r in rows if r['purpose'] != 'summary']
        elif defect == 'unpaired':
            args[4].summary(dict(event='context_summary_model_finished', sample_id='unpaired', input_tokens=50))
        else:
            next(r for r in rows if r['purpose'] == 'summary')['C'] = 999999
    monkeypatch.setattr(probe, 'execute', execute)
    assert asyncio.run(probe.run_probe('unused', 'fixture', tmp_path / 'report', live=True)) == (1 if defect == 'wrong_ratio' else 3)


def test_summary_failure_blocks_next_physical_send(monkeypatch, tmp_path):
    state = controlled_application(monkeypatch)
    async def execute(config, model_id, root, budget, measurements, report, save):
        budget.reserve('fixture')
        measurements.summary(dict(event='context_summary_model_finished', sample_id='failed', error_type='ReadTimeout'))
        budget.reserve('fixture')
        state.sends += 1
    monkeypatch.setattr(probe, 'execute', execute)
    path = tmp_path / 'report'
    assert asyncio.run(probe.run_probe('unused', 'fixture', path, live=True)) == 1
    assert state.sends == 0
    assert json.loads(path.read_text())['http']['used'] == 1


@pytest.mark.parametrize('reason, expected', [('summary_failed', 1), ('no_safe_cut', 3), ('summary_input_exceeds_budget', 3)])
def test_manual_execution_failure_is_not_uncovered(monkeypatch, tmp_path, reason, expected):
    controlled_application(monkeypatch)
    original = probe.open_component_application
    @asynccontextmanager
    async def opened(*args, **kwargs):
        async with original(*args, **kwargs) as app:
            async def failed(sid):
                handler = next(h for h in probe.logging.getLogger('naiyue_test_agent.agent_runtime.context_compaction').handlers if isinstance(h, probe.Measurements))
                handler.operations.append({'reason': reason})
                return {'compaction_status': 'failed'}
            app.host.runs.compact = failed
            yield app
    monkeypatch.setattr(probe, 'open_component_application', opened)
    assert asyncio.run(probe.run_probe('unused', 'fixture', tmp_path / 'report', live=True)) == expected


def test_runtime_summary_timeout_stops_before_fallback_agent(monkeypatch, tmp_path):
    import httpx
    from test_async_application import StreamingToolModel
    from test_context_inspector import model_with_limits
    from test_context_calibration_runtime import identified
    from naiyue_test_agent.model_access import ModelCatalog

    class TimeoutSummary(StreamingToolModel):
        async def _next(self, messages):
            if str(messages[0].content).startswith('Summarize completed'):
                raise httpx.ReadTimeout('synthetic')
            return await super()._next(messages)

    replies = [AIMessage(content='ACK', usage_metadata={
        'input_tokens': a, 'output_tokens': 1, 'total_tokens': a + 1}) for a in (10416, 13881)]
    base = identified(model_with_limits(replies))
    base.metadata.update(naiyue_compaction_reserve={'tokens': 110000, 'source': 'config'},
        naiyue_compaction_keep_recent={'tokens': 500, 'source': 'config'},
        naiyue_compaction_summary_output={'tokens': 2048, 'source': 'config'})
    chosen = TimeoutSummary(replies=replies, metadata=base.metadata)
    monkeypatch.setattr(probe, 'load_model_catalog', lambda _: ModelCatalog('A', {'A': chosen}))
    budget_holder = []
    def guard(catalog, budget):
        budget_holder[:] = [budget]
    monkeypatch.setattr(probe, 'guard_catalog', guard)
    original = TimeoutSummary._next
    async def guarded(self, messages):
        budget_holder[0].reserve('A')
        return await original(self, messages)
    monkeypatch.setattr(TimeoutSummary, '_next', guarded)
    path = tmp_path / 'report'
    assert asyncio.run(probe.run_probe('unused', 'A', path, live=True)) == 1
    report = json.loads(path.read_text())
    assert report['http']['used'] == 2  # C1 and failed summary; fallback never sent.
    assert len(chosen.seen) == 1
    assert report['scenarios'][1]['status'] == 'failed'
    assert all(s['status'] == 'not_run' for s in report['scenarios'][2:])
