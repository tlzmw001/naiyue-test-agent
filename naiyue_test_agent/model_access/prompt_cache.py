"""Translate declared cache capabilities into stable model constructor defaults."""


_RETENTIONS = {
    'openai': ('in_memory', '24h'),
    'openai_options': ('30m',),
    'anthropic': ('5m', '1h'),
}


def prompt_cache_model_settings(profile: dict, request: dict, kind: str) -> dict:
    """No model-name inference, IO, request context, or shared mutable policy."""
    capability = profile.get('prompt_cache', False)
    if capability is False:
        if 'prompt_cache' in request:
            raise ValueError('request.prompt_cache requires a supporting profile.prompt_cache')
        return {}
    if (not isinstance(capability, dict)
            or set(capability) != {'protocol', 'retentions'}):
        raise ValueError('profile.prompt_cache must contain protocol and retentions')
    protocol = capability['protocol']
    if not isinstance(protocol, str) or protocol not in _RETENTIONS:
        raise ValueError('profile.prompt_cache.protocol is unsupported')
    compatible = (kind in ('openai', 'openai_compatible') if protocol in ('openai', 'openai_options')
                  else kind == 'anthropic')
    if not compatible:
        raise ValueError('profile.prompt_cache.protocol does not match provider kind')
    retentions = capability['retentions']
    if (not isinstance(retentions, list) or not retentions
            or any(not isinstance(value, str) or value not in _RETENTIONS[protocol] for value in retentions)
            or len(set(retentions)) != len(retentions)):
        raise ValueError('profile.prompt_cache.retentions must be unique supported values')
    override = request.get('prompt_cache', {})
    if not isinstance(override, dict) or set(override) - {'retention'}:
        raise ValueError('request.prompt_cache accepts only retention')
    selected = next(value for value in _RETENTIONS[protocol] if value in retentions)
    if 'retention' in override:
        selected = override['retention']
        if not isinstance(selected, str) or selected not in retentions:
            raise ValueError('request.prompt_cache.retention is not supported by profile.prompt_cache')
    if protocol == 'openai':
        return {'model_kwargs': {'prompt_cache_retention': selected}}
    if protocol == 'openai_options':
        return {'prompt_cache_options': {'mode': 'implicit', 'ttl': selected}}
    return {'model_kwargs': {'cache_control': {'type': 'ephemeral', 'ttl': selected}}}
