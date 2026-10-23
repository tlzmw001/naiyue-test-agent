"""Bind one explicitly declared memory result protocol on the existing model."""

import json
import warnings

from langchain_core.messages import SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableBinding
from langchain_openai import ChatOpenAI

from . import bind_model_output_limit


def prepare_memory_output(model, schema, output_tokens):
    mode = (getattr(model, 'profile', None) or {}).get('memory_output', 'text')
    capped = bind_model_output_limit(model, output_tokens)
    if mode == 'text':
        return capped, mode, 0
    if mode not in ('json_schema', 'function_calling'):
        raise ValueError('memory_output_protocol_unavailable')
    options = {'strict': True} if isinstance(model, ChatOpenAI) else {}
    # The pinned SDK may warn and switch protocols for legacy models. That is
    # not permission to silently change the configured memory contract.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        structured = model.with_structured_output(schema, method=mode, include_raw=True, **options)
    if caught:
        raise ValueError('memory_output_protocol_unavailable')
    first = getattr(structured, 'first', None)
    raw = getattr(first, 'steps__', {}).get('raw')
    # include_raw in the pinned adapters is a parallel raw binding plus a parser.
    # Unsupported adapter composition fails before I/O rather than guessing its
    # hidden request cost or allowing an implicit protocol fallback.
    if not isinstance(raw, RunnableBinding):
        raise ValueError('memory_output_protocol_unavailable')
    declared = raw.kwargs.get('ls_structured_output_format', {}).get('kwargs', {}).get('method')
    if declared != mode:
        raise ValueError('memory_output_protocol_unavailable')
    # RunnableParallel does not forward arbitrary invoke kwargs to children.
    # Replace only this new wrapper's raw binding; the shared model is untouched.
    first.steps__['raw'] = capped.bound.bind(**(raw.kwargs | capped.kwargs))
    request = {key: value for key, value in raw.kwargs.items() if key != 'ls_structured_output_format'}
    overhead = count_tokens_approximately([SystemMessage(content=json.dumps(request, ensure_ascii=False))])
    return structured, mode, overhead
