"""Compare reported cache usage for two requests with one synthetic shared prefix."""

import argparse
import asyncio
import json
import logging
import math
import os

from naiyue_test_agent.config_paths import get_models_path


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--deadline-seconds', type=float, default=90)
    parser.add_argument('--output-tokens', type=int, default=128)
    parser.add_argument('--prefix-repetitions', type=int, default=120)
    args = parser.parse_args(argv)
    if (not math.isfinite(args.deadline_seconds) or args.deadline_seconds <= 0
            or args.output_tokens <= 0 or args.prefix_repetitions <= 0):
        parser.error('Probe budgets must be finite and positive')

    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'

    from langchain_openai import ChatOpenAI
    from langchain_anthropic import ChatAnthropic
    from naiyue_test_agent.model_access import load_model_catalog, bind_model_output_limit
    from naiyue_test_agent.model_access.usage import normalize_model_usage
    from naiyue_test_agent.agent_runtime.async_utils import await_settled
    from scripts.provider_acceptance_budget import RequestBudget, guard_catalog

    catalog, budget = None, RequestBudget(2)
    result = {'status': 'FAIL', 'requests': 0, 'cache_hit': None, 'usage': []}
    try:
        catalog = load_model_catalog(args.config)
        model = catalog.get(args.model_id)
        if (not isinstance(model, (ChatOpenAI, ChatAnthropic))
                or not isinstance((model.profile or {}).get('prompt_cache'), dict)):
            raise ValueError('Prompt-cache probe requires declared remote cache support')
        async with asyncio.timeout(args.deadline_seconds):
            guard_catalog(catalog, budget)
            selected = bind_model_output_limit(model, args.output_tokens)
            prefix = ('Synthetic diagnostic rules: preserve stable project instructions; '
                      'report only the requested short acknowledgement. ') * args.prefix_repetitions
            for number in (1, 2):
                response = await selected.ainvoke([('system', prefix),
                    ('user', f'Diagnostic request {number}. Reply with only OK.')],
                    **({'store': False} if isinstance(model, ChatOpenAI) else {}))
                result['usage'].append(normalize_model_usage(response.usage_metadata))
            result['status'] = 'PASS'
    except Exception as exc:
        result['error_type'] = type(exc).__name__
    finally:
        if catalog is not None:
            try:
                await await_settled(catalog.aclose())
            except Exception as exc:
                logging.getLogger(__name__).error('probe_catalog_close_failed error_type=%s', type(exc).__name__)
                result['status'] = 'FAIL'
                result.setdefault('error_type', type(exc).__name__)
    result['requests'] = budget.snapshot()['used']
    reads = [row['cache_read_tokens'] for row in result['usage']]
    result['cache_hit'] = (True if any(count is not None and count > 0 for count in reads)
                           else False if len(reads) == 2 and all(count is not None for count in reads) else None)
    print(json.dumps(result))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
