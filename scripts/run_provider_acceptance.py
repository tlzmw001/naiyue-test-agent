"""Explicit, non-retrying live acceptance with one bounded in-process ledger."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from .provider_acceptance_budget import RequestBudget
from .provider_acceptance_cases import CaseEnvironment, AcceptanceCheckFailed
from .provider_acceptance_preflight import preflight


async def run_acceptance(config, model_id, report_path, *, plan=None, continue_from=None,
                         continue_independent=False, request_limit=60):
    if plan is None:
        from .provider_acceptance_core import core_plan
        from .provider_acceptance_agents import agents_plan
        from .provider_acceptance_context import context_plan
        plan = core_plan(revalidate=continue_from is not None) + agents_plan(diagnose=continue_independent) + context_plan()
    total = RequestBudget(request_limit)
    history = {}
    if continue_from is not None:
        previous = json.loads(Path(continue_from).read_text())
        if previous['model_id'] != model_id or previous['budget']['stopped']:
            raise ValueError('Acceptance continuation model or stopped ledger mismatch')
        history = {(row['batch'], row['name']): row for row in previous['cases']}
        planned = {(batch, name) for batch, _, cases in plan for name, _, _ in cases}
        new_checks = {('100B', 'text_stream_after_fix'), ('100B', 'read_text_after_fix'),
                      ('100D', 'multicall_diagnostic'), ('100D', 'nonstream_tool_diagnostic'),
                      ('100F', 'root_approve_after_final_args'), ('100F', 'child_siblings_after_final_args')}
        if not set(history) <= planned or not planned - set(history) <= new_checks or len(history) != len(previous['cases']):
            raise ValueError('Acceptance continuation plan mismatch')
        used = 0
        for row in history.values():
            if row['status'] not in ('passed', 'failed', 'not_run'):
                raise ValueError('Invalid acceptance history state')
            if row['status'] == 'failed' and row.get('failed_check') not in ('stream_deltas', 'exact_read_answer') and not continue_independent:
                raise ValueError('Only the diagnosed stream identity finding may be continued')
            if row['status'] != 'not_run':
                count = row['http']['used']
                if type(count) is not int or count < 0 or row['http']['models'] != ({model_id: count} if count else {}):
                    raise ValueError('Invalid historical request counts')
                used += count
        if used != previous['budget']['used'] or previous['budget']['limit'] != request_limit:
            raise ValueError('Inconsistent historical total')
    report = {'status': 'running', 'model_id': model_id, 'api': 'responses',
              'sdk_retries': 0, 'budget': total.snapshot(), 'cases': [], 'batches': [],
              'not_covered': ['thinking_levels_unconfigured', 'chat_completions',
                              'other_providers', 'windows_linux_native', 'browser_manual_live']}
    # Exclusive creation means rerunning cannot overwrite earlier failed evidence.
    with Path(report_path).open('x', encoding='utf-8') as output:
        def save():
            report['budget'] = total.snapshot()
            output.seek(0)
            json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n')
            output.truncate()
            output.flush()
            os.fsync(output.fileno())
        save()
        stopped, current_findings = False, False
        with TemporaryDirectory(prefix='naiyue-acceptance-') as directory:
            for batch_name, limit, cases in plan:
                batch = total.child(limit)
                for name, case_limit, execute in cases:
                    row = {'batch': batch_name, 'name': name, 'status': 'not_run'}
                    prior = history.get((batch_name, name))
                    if prior is not None and prior['status'] != 'not_run':
                        case = batch.child(case_limit)
                        for _ in range(prior['http']['used']):
                            case.reserve(model_id)
                        report['cases'].append(prior)
                        save()
                        continue
                    report['cases'].append(row)
                    if stopped:
                        save()
                        continue
                    root = Path(directory).resolve() / name
                    root.mkdir()
                    case = batch.child(case_limit)
                    env = CaseEnvironment(config, model_id, root, case)
                    started = monotonic()
                    try:
                        async with asyncio.timeout(180):
                            checks = await execute(env)
                        row.update(status='passed', checks=checks)
                    except Exception as error:
                        row.update(status='failed', error_type=type(error).__name__)
                        # Assertion messages are authored fixed identifiers only.
                        if isinstance(error, AcceptanceCheckFailed):
                            row['failed_check'] = error.check
                        current_findings = True
                        stopped = not (continue_independent and isinstance(error, AcceptanceCheckFailed))
                    finally:
                        row.update(http=case.snapshot(), seconds=round(monotonic() - started, 3), **env.evidence())
                        save()
                    print(json.dumps({'case': name, 'status': row['status'], 'requests': case.snapshot()['used']},
                                     sort_keys=True), flush=True)
                report['batches'].append({'name': batch_name, **batch.snapshot()})
                save()
        historical_findings = any(row['status'] == 'failed' for row in history.values())
        report['status'] = 'failed' if stopped else 'completed_with_findings' if historical_findings or current_findings else 'passed'
        save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--continue-from', help='Explicitly continue only unexecuted cases; preserve historical sends and failures')
    parser.add_argument('--continue-independent', action='store_true', help='After diagnosis, collect independent assertions without retrying failed cases; network/control failures still halt')
    args = parser.parse_args(argv)
    # Protect provider payloads from SDK/http/tracing logs. Only finite receipts are printed.
    logging.disable(logging.CRITICAL)
    os.environ['LANGSMITH_TRACING'] = os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    ready = preflight(args.config, args.model_id)
    if ready['status'] != 'ready' or ready['api'] != 'responses':
        print(json.dumps({'status': 'blocked', 'reason': 'preflight_not_responses_ready'}))
        return 2
    result = asyncio.run(run_acceptance(args.config, args.model_id, args.report,
        continue_from=args.continue_from, continue_independent=args.continue_independent))
    print(json.dumps({'status': result['status'], 'http': result['budget']}, sort_keys=True))
    return 0 if result['status'] in ('passed', 'completed_with_findings') else 1


if __name__ == '__main__':
    raise SystemExit(main())
