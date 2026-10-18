"""Finite, content-free statistics from LangChain's already normalized usage."""

import logging


logger = logging.getLogger(__name__)
_TIERS = ('', 'priority_', 'flex_')


def _count(value, field):
    if value is None or type(value) is int and value >= 0:
        return value
    logger.warning('model_usage_invalid field=%s', field)
    return None


def _sum(details, keys, field):
    values = [_count(details[key], field) for key in keys if key in details]
    return sum(values) if values and all(value is not None for value in values) else None


def normalize_model_usage(usage):
    """Input includes cache subsets; never infer missing counts or mutate input."""
    result = dict.fromkeys(('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'))
    if usage is None:
        return result
    if not isinstance(usage, dict):
        logger.warning('model_usage_invalid field=usage')
        return result
    for field in ('input_tokens', 'output_tokens'):
        result[field] = _count(usage.get(field), field)
    details = usage.get('input_token_details')
    if details is None:
        return result
    if not isinstance(details, dict):
        logger.warning('model_usage_invalid field=input_token_details')
        return result
    result['cache_read_tokens'] = _sum(details, [tier + 'cache_read' for tier in _TIERS], 'cache_read_tokens')
    creation = dict(details)
    split = ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens')
    if any(key in details for key in split):
        amount = _sum(details, split, 'cache_write_tokens')
        if amount is None or amount > 0 or details.get('cache_creation') is None:
            creation['cache_creation'] = amount
    result['cache_write_tokens'] = _sum(creation, [tier + 'cache_creation' for tier in _TIERS], 'cache_write_tokens')
    if (result['input_tokens'] is not None
            and sum(result[key] or 0 for key in ('cache_read_tokens', 'cache_write_tokens')) > result['input_tokens']):
        logger.warning('model_usage_invalid field=cache_subsets')
        result.update(cache_read_tokens=None, cache_write_tokens=None)
    return result
