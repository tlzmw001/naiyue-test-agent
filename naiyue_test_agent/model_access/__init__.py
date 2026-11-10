"""Load local model configuration and construct LangChain chat models."""

from pathlib import Path

from threading import Lock

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from naiyue_test_agent.contracts.failures import normalize_classifiers
from .errors import describe_model_failure
from .messages import MODEL_INPUT_METADATA_KEY
from .responses_stream import ResponsesChatOpenAI
from .completions_reasoning import ReasoningChatOpenAI

from naiyue_test_agent.model_access.thinking import (
    MODEL_DEFAULT,
    THINKING_LEVELS,
    ThinkingSelection,
    native_thinking_settings,
    resolve_thinking_level,
    validate_supported_levels,
)


from .configuration import (
    ModelConfigError, DEFAULT_REQUEST_TIMEOUT_SECONDS, DEFAULT_SDK_MAX_RETRIES,
    DEFAULT_CONTEXT_WINDOW_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_COMPACTION_RESERVE_TOKENS,
    DEFAULT_COMPACTION_KEEP_RECENT_TOKENS, DEFAULT_COMPACTION_SUMMARY_OUTPUT_TOKENS,
    MODEL_LIMITS_METADATA_KEY, MODEL_ID_METADATA_KEY, COMPACTION_RESERVE_METADATA_KEY,
    COMPACTION_KEEP_RECENT_METADATA_KEY, COMPACTION_SUMMARY_OUTPUT_METADATA_KEY,
    read_model_configuration,
)
def bind_model_output_limit(model: BaseChatModel, output_tokens: int):
    """Apply a one-request output cap using the configured adapter's native name."""
    if type(output_tokens) is not int or output_tokens <= 0:
        raise ValueError('output_tokens must be a positive integer')
    if isinstance(model, ChatOllama):
        return model.model_copy(update={'num_predict': output_tokens}).bind()
    parameter = 'max_completion_tokens' if isinstance(model, ChatOpenAI) else 'max_tokens'
    return model.bind(**{parameter: output_tokens})


class ModelCatalog:
    """A validated, process-local snapshot of configured model objects."""

    def __init__(self, default_model_id: str, models: dict[str, BaseChatModel]):
        self.default_model_id = default_model_id
        self._models = models
        self._owned_http_clients = []
        self._thinking_selections: dict[str, ThinkingSelection] = {}
        self._thinking_capabilities = {}
        self._error_classifiers = {}
        self._error_lock = Lock()

    def configure_error_classifiers(self, model_id, classifiers):
        self.get(model_id)
        selected = normalize_classifiers(classifiers)
        with self._error_lock:
            self._error_classifiers[model_id] = selected

    def describe_failure(self, model_id, error):
        self.get(model_id)
        with self._error_lock:
            selected = self._error_classifiers.get(model_id, ())
        return describe_model_failure(error, classifiers=selected)

    def failure_describer(self):
        """Freeze model-specific mappings for a run; model switching keeps its own mapping."""
        with self._error_lock:
            selected = dict(self._error_classifiers)
        known = frozenset(self._models)

        def describe(model_id, error):
            if model_id not in known:
                raise ModelConfigError('Unknown model_id')
            return describe_model_failure(error, classifiers=selected.get(model_id, ()))

        return describe

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(self._models)

    def get(self, model_id: str) -> BaseChatModel:
        if not isinstance(model_id, str) or model_id not in self._models:
            raise ModelConfigError(f'Unknown model_id: {model_id}')
        return self._models[model_id]

    async def aclose(self):
        from .catalog_lifecycle import aclose_models
        await aclose_models(self._owned_http_clients)

    def prepare_async_clients(self, *, http_options=None, network=None):
        from .catalog_lifecycle import prepare_owned_clients
        if not self._owned_http_clients:
            prepare_owned_clients(self._models.values(), self._owned_http_clients,
                http_options=http_options, network=network)

    def thinking_selection(self, model_id: str) -> ThinkingSelection:
        self.get(model_id)
        return self._thinking_selections.get(
            model_id, ThinkingSelection(MODEL_DEFAULT, MODEL_DEFAULT, {}))

    def image_capabilities(self):
        return {identity: {'image_inputs': (getattr(model, 'profile', None) or {}).get('image_inputs') is True}
                for identity, model in self._models.items()}

    def thinking_options(self, model_id):
        selection = self.thinking_selection(model_id)
        capability = self._thinking_capabilities.get(model_id, {})
        return {'supported_levels': list(capability.get('levels', ())),
                'default_level': selection.selected}

    def with_thinking(self, model_id, level):
        model = self.get(model_id)
        if level == MODEL_DEFAULT:
            return model
        capability = self._thinking_capabilities.get(model_id, {})
        if level not in capability.get('levels', ()):
            raise ValueError('Unsupported thinking level')
        settings = native_thinking_settings(capability['kind'], level)
        if getattr(model, 'use_responses_api', False) and model.reasoning is not None:
            settings = {'reasoning_effort': None, 'reasoning': {**model.reasoning, 'effort': settings.pop('reasoning_effort')}, **settings}
        if capability['kind'] == 'anthropic':
            # Materialize lazy SDK properties before copying, so both views borrow them.
            model._client, model._async_client
            settings = {'thinking': None, 'reasoning_effort': None, **settings}
        return model.model_copy(update=settings)


def _build_model(kind: str, model_name: str, provider: dict, model_id: str,
                 thinking_settings: dict, request_settings: dict,
                 limit_metadata: dict, reserve_metadata: dict,
                 keep_metadata: dict, summary_output_metadata: dict,
                 api: str | None) -> BaseChatModel:
    try:
        request_settings = dict(request_settings)
        extension = request_settings.pop('_reasoning_output', None)
        summary = request_settings.pop('_reasoning_summary', None)
        if summary is not None:
            thinking_settings = dict(thinking_settings)
            effort = thinking_settings.pop('reasoning_effort', None)
            thinking_settings['reasoning'] = {'summary': summary, **({'effort': effort} if effort is not None else {})}
        output_limit = limit_metadata['max_output_tokens']
        metadata = {MODEL_ID_METADATA_KEY: model_id, MODEL_LIMITS_METADATA_KEY: limit_metadata,
                    COMPACTION_RESERVE_METADATA_KEY: reserve_metadata,
                    COMPACTION_KEEP_RECENT_METADATA_KEY: keep_metadata,
                    COMPACTION_SUMMARY_OUTPUT_METADATA_KEY: summary_output_metadata,
                    MODEL_INPUT_METADATA_KEY: {'kind': kind, 'api': api}}
        openai_model = ResponsesChatOpenAI if api == 'responses' else ReasoningChatOpenAI if extension else ChatOpenAI
        if kind == 'openai':
            return openai_model(model=model_name, api_key=provider['api_key'], base_url=provider.get('base_url'),
                              max_tokens=output_limit, metadata=metadata, use_responses_api=api == 'responses',
                              **thinking_settings, **request_settings)
        if kind == 'openai_compatible':
            return openai_model(model=model_name, api_key=provider['api_key'],
                              base_url=provider['base_url'], max_tokens=output_limit,
                              metadata=metadata, use_responses_api=api == 'responses',
                              **thinking_settings, **request_settings)
        if kind == 'anthropic':
            return ChatAnthropic(model=model_name, api_key=provider['api_key'], base_url=provider.get('base_url'),
                                 max_tokens=output_limit, metadata=metadata,
                                 **thinking_settings, **request_settings)
        return ChatOllama(model=model_name, base_url=provider['base_url'],
                          client_kwargs={'timeout': request_settings['timeout'],**provider.get('client_kwargs',{})},
                          num_predict=output_limit, metadata=metadata,
                          **thinking_settings)
    except Exception as exc:
        raise ModelConfigError(
            f'models.{model_id}: model construction failed ({type(exc).__name__})'
        ) from None


def catalog_from_configuration(configuration, *, connections=None):
    """Construct the existing adapters; internal connections never change user schema."""
    models={}
    for model_id, definition in configuration.models.items():
        selection=definition['selection'];profile=definition['profile']
        provider=dict(definition['provider'])
        if connections is not None:provider.update(connections[model_id])
        model=_build_model(definition['kind'],definition['model_name'],provider,model_id,
            selection.native_settings,definition['request_settings'],definition['limit_metadata'],
            definition['reserve_metadata'],definition['keep_metadata'],definition['summary_output_metadata'],definition['api'])
        model.profile={**(model.profile or {}),**profile}
        models[model_id]=model
    catalog=ModelCatalog(configuration.default_model_id,models)
    catalog._thinking_selections=configuration.thinking_selections
    catalog._thinking_capabilities=configuration.thinking_capabilities
    return catalog


def load_model_catalog(path: str | Path | None = None) -> ModelCatalog:
    """Read, validate, and construct models without making model API requests."""
    return catalog_from_configuration(read_model_configuration(path))
