"""Zero-request provider acceptance preflight; no live execution mode."""

import argparse
import json

from naiyue_test_agent.config_paths import get_models_path
from .provider_acceptance_preflight import preflight


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(get_models_path()))
    parser.add_argument('--model-id')
    args = parser.parse_args(argv)
    result = preflight(args.config, args.model_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['status'] == 'ready' else 2


if __name__ == '__main__':
    raise SystemExit(main())
