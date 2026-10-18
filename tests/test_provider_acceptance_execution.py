"""Live acceptance orchestration is bounded, explicit and reports no raw errors."""

import asyncio
import json

import pytest

from scripts.run_provider_acceptance import run_acceptance, main
from scripts.provider_acceptance_cases import require, paired_tools
from scripts.provider_acceptance_budget import RequestBudget
from langchain_core.messages import AIMessage, ToolMessage


def test_pairing_requires_real_result_id_status_and_exact_order():
    call = AIMessage(content='', tool_calls=[{'name': 'read', 'args': {}, 'id': 'one'}])
    result = ToolMessage(content='fixture', tool_call_id='one', name='read')
    assert paired_tools([call, result], ['read'])[0] == result
    with pytest.raises(AssertionError):
        paired_tools([call], ['read'])
    with pytest.raises(AssertionError):
        paired_tools([call, result.model_copy(update={'tool_call_id': 'other'})], ['read'])


def test_runner_stops_failed_batch_and_retains_actual_budget_without_raw_error(tmp_path):
    calls = []
    async def good(env):
        env.budget.reserve('synthetic')
        calls.append('good')
        return {'checked': True}
    async def bad(env):
        env.budget.reserve('synthetic')
        raise ValueError('PRIVATE_URL_PRIVATE_CREDENTIAL')
    async def forbidden(env):
        calls.append('forbidden')
    path = tmp_path / 'receipt.json'
    plan = [('100B', 8, [('good', 1, good), ('bad', 1, bad), ('later', 1, forbidden)]),
            ('100C', 18, [('next', 1, forbidden)])]
    result = asyncio.run(run_acceptance('unused', 'synthetic', path, plan=plan))
    assert result['status'] == 'failed' and result['budget']['used'] == 2
    assert [row['status'] for row in result['cases']] == ['passed', 'failed', 'not_run', 'not_run']
    assert calls == ['good'] and json.loads(path.read_text()) == result
    assert 'PRIVATE' not in path.read_text()
    with pytest.raises(FileExistsError):
        asyncio.run(run_acceptance('unused', 'synthetic', path, plan=plan))


def test_assertion_names_are_fixed_and_cli_requires_live_opt_in(tmp_path):
    with pytest.raises(AssertionError, match='paired_results'):
        require(False, 'paired_results')
    with pytest.raises(SystemExit) as stopped:
        main(['--config', 'unused', '--model-id', 'synthetic', '--report', str(tmp_path / 'x')])
    assert stopped.value.code == 2


def test_explicit_continuation_counts_history_and_never_replays_finished_case(tmp_path):
    called = []
    async def historical_failure(env):
        env.budget.reserve('synthetic')
        require(False, 'stream_deltas')
    async def remaining(env):
        called.append('remaining')
        env.budget.reserve('synthetic')
        return {'verified': True}
    plan = [('100B', 8, [('first', 1, historical_failure), ('remaining', 2, remaining)])]
    first = tmp_path / 'first.json'
    asyncio.run(run_acceptance('unused', 'synthetic', first, plan=plan))
    report = asyncio.run(run_acceptance('unused', 'synthetic', tmp_path / 'continued.json',
        plan=plan, continue_from=first))
    assert called == ['remaining'] and report['budget']['used'] == 2
    assert report['cases'][0]['status'] == 'failed' and report['cases'][1]['status'] == 'passed'
    assert report['status'] == 'completed_with_findings'
    assert json.loads(first.read_text())['budget']['used'] == 1


def test_continuation_rejects_changed_model_or_inconsistent_ledger(tmp_path):
    async def done(env):
        env.budget.reserve('synthetic')
        return {}
    plan = [('100B', 8, [('first', 1, done)])]
    first = tmp_path / 'first.json'
    asyncio.run(run_acceptance('unused', 'synthetic', first, plan=plan))
    with pytest.raises(ValueError):
        asyncio.run(run_acceptance('unused', 'different', tmp_path / 'wrong.json', plan=plan, continue_from=first))
    data = json.loads(first.read_text())
    data['budget']['used'] = 0
    first.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        asyncio.run(run_acceptance('unused', 'synthetic', tmp_path / 'wrong.json', plan=plan, continue_from=first))


def test_explicit_independent_cases_continue_after_failure_without_repeating_it(tmp_path):
    seen = []
    async def branch_absent(env):
        env.budget.reserve('synthetic')
        seen.append('failed_once')
        require(False, 'root_approval_requested')
    async def independent(env):
        env.budget.reserve('synthetic')
        seen.append('next_once')
        return {'checked': True}
    plan = [('100C', 18, [('first', 2, branch_absent), ('next', 2, independent)])]
    result = asyncio.run(run_acceptance('unused', 'synthetic', tmp_path / 'all.json', plan=plan,
        continue_independent=True))
    assert seen == ['failed_once', 'next_once']
    assert result['status'] == 'completed_with_findings' and result['budget']['used'] == 2
