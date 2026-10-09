"""Compare reported cache usage for two requests with one synthetic shared prefix."""

import argparse
import os
import signal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='.env/models.json')
    parser.add_argument('--model-id', required=True)
    args = parser.parse_args()

    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'

    def on_timeout(_signum, _frame):
        raise TimeoutError('Prompt-cache probe deadline exceeded')

    signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(90)

    from langchain_openai import ChatOpenAI
    from naiyue_test_agent.model_access import load_model_catalog

    try:
        model = load_model_catalog(args.config).get(args.model_id)
        if not isinstance(model, ChatOpenAI):
            raise ValueError('Prompt-cache probe requires a ChatOpenAI model')

        shared_prefix = (
            'Synthetic discount contract: rule R42 applies a ten percent reduction '
            'to eligible diagnostic items; rule R43 excludes already discounted items. '
        ) * 120
        usage_rows = []
        for number in (1, 2):
            response = model.invoke(
                [('system', shared_prefix),
                 ('user', f'Diagnostic request {number}. Reply with only OK.')],
                max_tokens=128, store=False,
            )
            usage = response.usage_metadata or {}
            details = usage.get('input_token_details') or {}
            usage_rows.append((usage.get('input_tokens'), details.get('cache_read')))
    except Exception as exc:
        print(f'status=FAIL error_type={type(exc).__name__}')
        return 1
    else:
        for number, (input_tokens, cache_read_tokens) in enumerate(usage_rows, 1):
            print(f'request={number} input_tokens={input_tokens} '
                  f'cache_read_tokens={cache_read_tokens}')
        print('status=PASS requests=2')
        return 0
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
