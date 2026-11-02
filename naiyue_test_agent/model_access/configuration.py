"""Shared pure model configuration parsing; private provider values never enter public profiles."""
from dataclasses import dataclass,field
import json
import math
import stat
from pathlib import Path
from urllib.parse import urlsplit

from naiyue_test_agent.config_paths import get_models_path
from .thinking import (MODEL_DEFAULT, THINKING_LEVELS, ThinkingSelection, native_thinking_settings,
    resolve_thinking_level, validate_supported_levels)
from .prompt_cache import prompt_cache_model_settings

DEFAULT_REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_SDK_MAX_RETRIES = 2
DEFAULT_CONTEXT_WINDOW_TOKENS = 128000
DEFAULT_MAX_OUTPUT_TOKENS = 16384
DEFAULT_COMPACTION_RESERVE_TOKENS = 16384
DEFAULT_COMPACTION_KEEP_RECENT_TOKENS = 20000
DEFAULT_COMPACTION_SUMMARY_OUTPUT_TOKENS = 2048
MODEL_LIMITS_METADATA_KEY = 'naiyue_model_limits'
MODEL_ID_METADATA_KEY = 'naiyue_model_id'
COMPACTION_RESERVE_METADATA_KEY = 'naiyue_compaction_reserve'
COMPACTION_KEEP_RECENT_METADATA_KEY = 'naiyue_compaction_keep_recent'
COMPACTION_SUMMARY_OUTPUT_METADATA_KEY = 'naiyue_compaction_summary_output'


class ModelConfigError(ValueError):
    """A local model configuration cannot be used safely."""


@dataclass(frozen=True)
class ParsedModelConfiguration:
    default_model_id: str
    models: dict = field(repr=False)
    thinking_selections: dict = field(repr=False)
    thinking_capabilities: dict = field(repr=False)
    public_configuration: dict = field(repr=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ModelConfigError('Configuration has a duplicate JSON key')
        result[key] = value
    return result


def _object(value, label: str, required: set[str], allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ModelConfigError(f'{label} must be a JSON object')
    missing = required - value.keys()
    extra = value.keys() - allowed
    if missing:
        raise ModelConfigError(f'{label} is missing {sorted(missing)[0]}')
    if extra:
        raise ModelConfigError(f'{label} has unknown field {sorted(extra)[0]}')
    return value


def _nonempty(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelConfigError(f'{label} must be a non-empty string')
    return value


def _base_url(value, label: str, kind: str) -> str:
    url = _nonempty(value, label)
    try:
        parsed = urlsplit(url)
        port = parsed.port  # Access rejects malformed port values.
        valid = (parsed.scheme in ({'http', 'https'} if kind == 'ollama' else {'https'})
                 and parsed.hostname and not parsed.username and not parsed.password
                 and (port is None or port > 0) and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ModelConfigError(f'{label} must be a valid service base_url')
    return url


def read_model_configuration(path=None):
    config_path = Path(path) if path is not None else get_models_path()
    try:
        file_stat = config_path.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            raise ModelConfigError(f'{config_path}: model configuration must be a regular file')
        data = json.loads(config_path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except OSError as exc:
        raise ModelConfigError(f'{config_path}: cannot read model configuration ({type(exc).__name__})') from None
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ModelConfigError(f'{config_path}: invalid JSON ({type(exc).__name__})') from None

    return parse_model_configuration(data)


def parse_model_configuration(data, *, provider_connections=None):
    root = _object(data, 'config', {'schema_version', 'default_model_id', 'providers', 'models'},
                   {'schema_version', 'default_model_id', 'providers', 'models'})
    if type(root['schema_version']) is not int or root['schema_version'] != 1:
        raise ModelConfigError('config.schema_version must be 1')
    default_id = _nonempty(root['default_model_id'], 'config.default_model_id')
    providers = root['providers']
    models = root['models']
    if not isinstance(providers, dict) or not providers:
        raise ModelConfigError('config.providers must be a non-empty JSON object')
    if not isinstance(models, dict) or not models:
        raise ModelConfigError('config.models must be a non-empty JSON object')

    checked_providers = {}
    for provider_id, raw in providers.items():
        _nonempty(provider_id, 'provider_id')
        if provider_connections is not None:
            raw={**_object(raw,f'providers.{provider_id}',{'kind'},{'kind'}),**provider_connections[provider_id]}
        provider = _object(raw, f'providers.{provider_id}', {'kind'},
                           {'kind', 'api_key', 'base_url'})
        kind = provider['kind']
        if not isinstance(kind, str) or kind not in {
            'openai', 'openai_compatible', 'anthropic', 'ollama'
        }:
            raise ModelConfigError(f'providers.{provider_id}.kind is unsupported')
        if kind == 'ollama':
            required, allowed = {'kind', 'base_url'}, {'kind', 'base_url'}
        elif kind == 'openai_compatible':
            required = allowed = {'kind', 'api_key', 'base_url'}
        else:
            required = allowed = {'kind', 'api_key'}
        if provider_connections is not None:allowed=allowed|{'base_url'}
        provider = _object(provider, f'providers.{provider_id}', required, allowed)
        if 'api_key' in provider:
            _nonempty(provider['api_key'], f'providers.{provider_id}.api_key')
        if 'base_url' in provider:
            _base_url(provider['base_url'], f'providers.{provider_id}.base_url', 'ollama' if provider_connections is not None else kind)
        checked_providers[provider_id] = provider

    built_models = {}
    thinking_selections = {}
    thinking_capabilities = {}
    for model_id, raw in models.items():
        _nonempty(model_id, 'model_id')
        entry = _object(raw, f'models.{model_id}', {'provider_id', 'model'},
                        {'provider_id', 'model', 'thinking', 'request',
                         'context_window_tokens', 'max_output_tokens',
                         'compaction_reserve_tokens',
                         'compaction_keep_recent_tokens',
                         'compaction_summary_output_tokens', 'api', 'profile'})
        provider_id = _nonempty(entry['provider_id'], f'models.{model_id}.provider_id')
        model_name = _nonempty(entry['model'], f'models.{model_id}.model')
        if provider_id not in checked_providers:
            raise ModelConfigError(f'models.{model_id}.provider_id is unknown')
        provider = checked_providers[provider_id]
        api = entry.get('api')
        if provider['kind'] in ('openai', 'openai_compatible'):
            if not isinstance(api, str) or api not in ('chat_completions', 'responses'):
                raise ModelConfigError(f'models.{model_id}.api must be chat_completions or responses')
        elif 'api' in entry:
            raise ModelConfigError(f'models.{model_id}.api only applies to OpenAI adapters')
        profile = _object(entry.get('profile', {}), f'models.{model_id}.profile', set(),
                          {'image_inputs', 'prompt_cache', 'reasoning_output', 'memory_output'})
        if 'memory_output' in profile and profile['memory_output'] not in ('text', 'json_schema', 'function_calling'):
            raise ModelConfigError(f'models.{model_id}.profile.memory_output must be text, json_schema or function_calling')
        if 'image_inputs' in profile and type(profile['image_inputs']) is not bool:
            raise ModelConfigError(f'models.{model_id}.profile.image_inputs must be a boolean')
        if 'reasoning_output' in profile and (profile['reasoning_output'] != 'reasoning_content' or api != 'chat_completions'):
            raise ModelConfigError(f'models.{model_id}.profile.reasoning_output requires reasoning_content Chat Completions')
        limit_metadata = {}
        for field, default, source_field in (
            ('context_window_tokens', DEFAULT_CONTEXT_WINDOW_TOKENS,
             'context_window_source'),
            ('max_output_tokens', DEFAULT_MAX_OUTPUT_TOKENS, 'max_output_source'),
        ):
            value = entry.get(field, default)
            if type(value) is not int or value <= 0:
                raise ModelConfigError(f'models.{model_id}.{field} must be a positive integer')
            limit_metadata[field] = value
            limit_metadata[source_field] = 'config' if field in entry else 'default'
        limit_metadata['model_id'] = model_id
        default_reserve = min(DEFAULT_COMPACTION_RESERVE_TOKENS,
                              max(1, limit_metadata['context_window_tokens'] // 4))
        reserve_tokens = entry.get('compaction_reserve_tokens', default_reserve)
        if type(reserve_tokens) is not int or reserve_tokens <= 0:
            raise ModelConfigError(
                f'models.{model_id}.compaction_reserve_tokens must be a positive integer'
            )
        reserve_metadata = {
            'tokens': reserve_tokens,
            'source': 'config' if 'compaction_reserve_tokens' in entry else 'default',
        }
        default_keep = min(DEFAULT_COMPACTION_KEEP_RECENT_TOKENS,
                           max(1, limit_metadata['context_window_tokens'] // 4))
        keep_tokens = entry.get('compaction_keep_recent_tokens', default_keep)
        if type(keep_tokens) is not int or keep_tokens <= 0:
            raise ModelConfigError(
                f'models.{model_id}.compaction_keep_recent_tokens must be a positive integer'
            )
        keep_metadata = {
            'tokens': keep_tokens,
            'source': 'config' if 'compaction_keep_recent_tokens' in entry else 'default',
        }
        default_summary_output = min(
            DEFAULT_COMPACTION_SUMMARY_OUTPUT_TOKENS,
            limit_metadata['max_output_tokens'],
            max(1, reserve_tokens * 4 // 5),
        )
        summary_output_tokens = entry.get('compaction_summary_output_tokens',
                                          default_summary_output)
        if (type(summary_output_tokens) is not int or summary_output_tokens <= 0
                or summary_output_tokens > limit_metadata['max_output_tokens']
                or summary_output_tokens > reserve_tokens):
            raise ModelConfigError(
                f'models.{model_id}.compaction_summary_output_tokens must be '
                'positive and within output/reserve limits'
            )
        summary_output_metadata = {
            'tokens': summary_output_tokens,
            'source': ('config' if 'compaction_summary_output_tokens' in entry
                       else 'default'),
        }
        request = {}
        if 'request' in entry:
            request = _object(entry['request'], f'models.{model_id}.request', set(),
                              {'timeout_seconds', 'sdk_max_retries', 'prompt_cache', 'reasoning_summary',
                               'tool_stream', 'stream_chunk_timeout_seconds'})
        timeout_seconds = request.get('timeout_seconds', DEFAULT_REQUEST_TIMEOUT_SECONDS)
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise ModelConfigError(f'models.{model_id}.request.timeout_seconds must be positive and finite')
        sdk_max_retries = request.get('sdk_max_retries', DEFAULT_SDK_MAX_RETRIES)
        if type(sdk_max_retries) is not int or sdk_max_retries < 0:
            raise ModelConfigError(f'models.{model_id}.request.sdk_max_retries must be a non-negative integer')
        if provider['kind'] == 'ollama' and 'sdk_max_retries' in request:
            raise ModelConfigError(f'models.{model_id}.request.sdk_max_retries is unsupported for ollama')
        request_settings = {'timeout': timeout_seconds, 'max_retries': sdk_max_retries}
        if 'tool_stream' in request:
            if provider['kind'] != 'openai_compatible' or api != 'chat_completions':
                raise ModelConfigError(
                    f'models.{model_id}.request.tool_stream requires openai_compatible chat_completions')
            if type(request['tool_stream']) is not bool:
                raise ModelConfigError(f'models.{model_id}.request.tool_stream must be a boolean')
            request_settings['extra_body'] = {'tool_stream': request['tool_stream']}
        if 'stream_chunk_timeout_seconds' in request:
            if provider['kind'] not in ('openai', 'openai_compatible'):
                raise ModelConfigError(
                    f'models.{model_id}.request.stream_chunk_timeout_seconds requires an OpenAI adapter')
            chunk_timeout = request['stream_chunk_timeout_seconds']
            if (type(chunk_timeout) not in (int, float) or not math.isfinite(chunk_timeout)
                    or chunk_timeout < 0):
                raise ModelConfigError(
                    f'models.{model_id}.request.stream_chunk_timeout_seconds must be non-negative and finite')
            request_settings['stream_chunk_timeout'] = chunk_timeout
        try:
            request_settings.update(prompt_cache_model_settings(profile, request, provider['kind']))
        except ValueError as exc:
            raise ModelConfigError(f'models.{model_id}.{exc}') from None
        if 'reasoning_summary' in request:
            if api != 'responses' or request['reasoning_summary'] not in ('auto', 'concise', 'detailed'):
                raise ModelConfigError(f'models.{model_id}.request.reasoning_summary requires a supported Responses summary')
            request_settings['_reasoning_summary'] = request['reasoning_summary']
        if 'reasoning_output' in profile:
            request_settings['_reasoning_output'] = profile['reasoning_output']
        selection = ThinkingSelection(MODEL_DEFAULT, MODEL_DEFAULT, {})
        if 'thinking' in entry:
            thinking = _object(entry['thinking'], f'models.{model_id}.thinking',
                               {'supported_levels', 'default_level'},
                               {'supported_levels', 'default_level'})
            try:
                supported = validate_supported_levels(thinking['supported_levels'])
            except ValueError as exc:
                raise ModelConfigError(f'models.{model_id}.thinking.{exc}') from None
            requested = thinking['default_level']
            if not isinstance(requested, str) or requested not in (*THINKING_LEVELS, MODEL_DEFAULT):
                raise ModelConfigError(f'models.{model_id}.thinking.default_level is invalid')
            try:
                for level in supported:
                    native_thinking_settings(provider['kind'], level)
                selected = resolve_thinking_level(requested, supported)
                native_settings = native_thinking_settings(provider['kind'], selected)
            except ValueError as exc:
                raise ModelConfigError(f'models.{model_id}.thinking: {exc}') from None
            selection = ThinkingSelection(requested, selected, native_settings)
            thinking_capabilities[model_id] = {'kind': provider['kind'], 'levels': supported}
        built_models[model_id] = dict(kind=provider['kind'], model_name=model_name, provider=provider,
            model_id=model_id, selection=selection, request_settings=request_settings,
            limit_metadata=limit_metadata, reserve_metadata=reserve_metadata, keep_metadata=keep_metadata,
            summary_output_metadata=summary_output_metadata, api=api, profile=profile)
        thinking_selections[model_id] = selection
    if default_id not in built_models:
        raise ModelConfigError('config.default_model_id is unknown')
    public = json.loads(json.dumps(data))
    public['providers'] = {key: {'kind': value['kind']} for key,value in checked_providers.items()}
    return ParsedModelConfiguration(default_id, built_models, thinking_selections, thinking_capabilities, public)
