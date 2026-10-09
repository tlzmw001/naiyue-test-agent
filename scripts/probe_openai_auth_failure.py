"""Check one configured gateway's SDK error for a generated invalid credential."""

import argparse
import os
import secrets
import signal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='.env/models.json')
    parser.add_argument('--model-id', required=True)
    args = parser.parse_args()

    os.environ['LANGSMITH_TRACING'] = 'false'
    os.environ['LANGCHAIN_TRACING_V2'] = 'false'

    def on_timeout(_signum, _frame):
        raise TimeoutError('Authentication probe deadline exceeded')

    signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(30)

    from langchain_openai import ChatOpenAI
    from openai import AuthenticationError
    from naiyue_test_agent.model_access import load_model_catalog
    from naiyue_test_agent.model_access.errors import classify_model_failure

    try:
        configured = load_model_catalog(args.config).get(args.model_id)
        if not isinstance(configured, ChatOpenAI) or not configured.openai_api_base:
            raise ValueError('Authentication probe requires an OpenAI-compatible model')
        probe_model = ChatOpenAI(
            model=configured.model_name,
            base_url=str(configured.openai_api_base),
            api_key='invalid-diagnostic-' + secrets.token_hex(16),
            use_responses_api=True,
            max_retries=0,
            timeout=15,
            store=False,
        )
        probe_model.invoke('Authentication diagnostic. Reply OK.', max_tokens=64)
    except AuthenticationError as exc:
        category, reason = classify_model_failure(exc)
        print(f'status=PASS error_type=AuthenticationError http_status={exc.status_code} '
              f'category={category} reason={reason}')
        return 0
    except Exception as exc:
        print(f'status=FAIL error_type={type(exc).__name__}')
        return 1
    else:
        print('status=FAIL error_type=UnexpectedSuccess')
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
