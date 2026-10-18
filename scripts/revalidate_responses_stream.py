"""Two explicit phases under one nine-send ledger; historical 52 sends stay separate."""

import argparse
import asyncio
import json
import logging
import os

from .provider_acceptance_cases import AcceptanceCheckFailed
from .provider_acceptance_multicall import multicall_diagnostic
from .provider_acceptance_core import approve
from .provider_acceptance_agents import siblings
from .provider_acceptance_preflight import preflight
from .run_provider_acceptance import run_acceptance


async def diagnose(env):
    try:
        return await multicall_diagnostic(env)
    except AcceptanceCheckFailed as error:
        if error.check != 'diagnostic_lc_matches_native':
            raise
        return {'confirmed_final_argument_gap': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--phase', choices=('diagnose', 'validate'), required=True)
    parser.add_argument('--continue-from')
    args = parser.parse_args(argv)
    if (args.phase == 'validate') != bool(args.continue_from):
        parser.error('validate requires the diagnosis ledger; diagnose starts a new ledger')
    logging.disable(logging.CRITICAL)
    os.environ['LANGSMITH_TRACING'] = os.environ['LANGCHAIN_TRACING_V2'] = 'false'
    ready = preflight(args.config, args.model_id)
    if ready['status'] != 'ready' or ready['api'] != 'responses':
        print(json.dumps({'status': 'blocked', 'reason': 'preflight_not_responses_ready'}))
        return 2
    cases = [('final_argument_events', 1, diagnose)]
    if args.phase == 'validate':
        cases += [('root_approve_after_final_args', 2, approve),
                  ('child_siblings_after_final_args', 6, siblings)]
    result = asyncio.run(run_acceptance(args.config, args.model_id, args.report,
        plan=[('100F', 9, cases)], request_limit=9, continue_from=args.continue_from))
    print(json.dumps({'status': result['status'], 'http': result['budget']}, sort_keys=True))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
