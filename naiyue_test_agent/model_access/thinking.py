"""Resolve project thinking levels and map them to provider adapter settings."""

from dataclasses import dataclass
from typing import Any


THINKING_LEVELS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
MODEL_DEFAULT = 'model_default'


@dataclass(frozen=True)
class ThinkingSelection:
    requested: str
    selected: str
    native_settings: dict[str, Any]


def validate_supported_levels(levels: object) -> tuple[str, ...]:
    if (not isinstance(levels, list) or not levels
            or any(not isinstance(level, str) or level not in THINKING_LEVELS for level in levels)
            or len(set(levels)) != len(levels)):
        raise ValueError('supported_levels must contain unique project thinking levels')
    return tuple(levels)


def resolve_thinking_level(requested: str, supported_levels: tuple[str, ...]) -> str:
    if requested == MODEL_DEFAULT:
        return MODEL_DEFAULT
    if requested not in THINKING_LEVELS:
        raise ValueError('default_level must be a project thinking level or model_default')
    if not supported_levels:
        raise ValueError('supported_levels must not be empty')
    if requested in supported_levels:
        return requested

    requested_index = THINKING_LEVELS.index(requested)
    for level in THINKING_LEVELS[requested_index + 1:]:
        if level in supported_levels:
            return level
    for level in reversed(THINKING_LEVELS[:requested_index]):
        if level in supported_levels:
            return level
    raise ValueError('supported_levels has no usable thinking level')


def native_thinking_settings(kind: str, selected: str) -> dict[str, Any]:
    if selected == MODEL_DEFAULT:
        return {}
    if kind in {'openai', 'openai_compatible'}:
        return {'reasoning_effort': 'none' if selected == 'off' else selected}
    if kind == 'anthropic':
        if selected == 'off':
            return {'thinking': {'type': 'disabled'}}
        if selected == 'minimal':
            raise ValueError('anthropic does not have a native minimal effort setting')
        return {'reasoning_effort': selected}
    if kind == 'ollama':
        if selected == 'off':
            return {'reasoning': False}
        if selected not in {'low', 'medium', 'high', 'max'}:
            raise ValueError(f'ollama does not have a documented {selected} thinking level')
        return {'reasoning': selected}
    raise ValueError('unknown provider kind for thinking settings')
