"""Content-free identities derived from constructed native adapters."""
import hashlib
import json
import logging
from importlib.metadata import version

KEY = 'naiyue_context_estimation'
logger = logging.getLogger(__name__)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def attach_identity(model, *, model_id, kind, api, route=None):
    try:
        if route is None:
            if kind in ('openai', 'openai_compatible'):
                route = str(model.root_client.base_url)
            elif kind == 'anthropic':
                route = str(model._client.base_url)
            else:
                route = str(model._client._client.base_url)
        thinking = {name: getattr(model, name, None) for name in
                    ('reasoning', 'reasoning_effort', 'thinking', 'think')}
        data = {'version': 1, 'model_id': model_id,
            'request_model': getattr(model, 'model_name', None) or getattr(model, 'model', None),
            'kind': kind, 'api': api, 'route_fingerprint': fingerprint(route.rstrip('/')),
            'thinking_fingerprint': fingerprint(thinking),
            'input_profile_fingerprint': fingerprint({key: value for key, value in
                (model.profile or {}).items() if key in ('image_inputs', 'reasoning_output')}),
            'adapter_fingerprint': fingerprint({'projection': 1, 'estimator': 1,
                'core': version('langchain-core'), 'adapter': version(
                    'langchain-anthropic' if kind == 'anthropic' else
                    'langchain-ollama' if kind == 'ollama' else 'langchain-openai')})}
    except Exception as error:
        logger.warning('calibration_identity_unavailable error_type=%s', type(error).__name__)
        data = None
    model.metadata = {**(model.metadata or {}), KEY: data}


def refresh_thinking(model):
    data = (model.metadata or {}).get(KEY)
    if data:
        thinking = {name: getattr(model, name, None) for name in
                    ('reasoning', 'reasoning_effort', 'thinking', 'think')}
        model.metadata = {**model.metadata, KEY: {**data,
            'thinking_fingerprint': fingerprint(thinking)}}
