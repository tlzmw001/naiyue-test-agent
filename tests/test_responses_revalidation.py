"""Diagnosis and validation share a bounded ledger, never replay the diagnosis."""

import asyncio
import json

import pytest

from scripts.run_provider_acceptance import run_acceptance
from scripts.revalidate_responses_stream import main


def test_two_phases_keep_the_nine_send_limit_and_first_receipt(tmp_path):
    called = []
    def case(name, count):
        async def execute(env):
            called.append(name)
            for _ in range(count):
                env.budget.reserve('synthetic')
            return {'verified': True}
        return execute
    first_path, last_path = tmp_path / 'diagnosis.json', tmp_path / 'validation.json'
    diagnosis = [('final_argument_events', 1, case('diagnosis', 1))]
    first = asyncio.run(run_acceptance('unused', 'synthetic', first_path,
        plan=[('100F', 9, diagnosis)], request_limit=9))
    validation = diagnosis + [('root_approve_after_final_args', 2, case('root', 2)),
                             ('child_siblings_after_final_args', 6, case('siblings', 6))]
    last = asyncio.run(run_acceptance('unused', 'synthetic', last_path,
        plan=[('100F', 9, validation)], request_limit=9, continue_from=first_path))
    assert called == ['diagnosis', 'root', 'siblings']
    assert first['budget']['used'] == 1 and last['budget']['used'] == last['budget']['limit'] == 9
    assert last['cases'][0] == first['cases'][0]
    assert json.loads(first_path.read_text()) == first
    assert last['status'] == 'passed'


def test_validate_requires_a_saved_ledger_and_live_opt_in(tmp_path):
    base = ['--config', 'unused', '--model-id', 'synthetic', '--report', str(tmp_path / 'x')]
    with pytest.raises(SystemExit) as caught:
        main(base + ['--phase', 'validate', '--live'])
    assert caught.value.code == 2
    with pytest.raises(SystemExit) as caught:
        main(base + ['--phase', 'diagnose'])
    assert caught.value.code == 2
