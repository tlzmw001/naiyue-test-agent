"""Visible provider text only; signatures/encrypted fields stay in canonical history."""


import logging

logger = logging.getLogger(__name__)


def visible_reasoning(message):
    parts = []
    try:
        blocks = message.content_blocks
    except (TypeError, ValueError, KeyError) as error:
        logger.warning('reasoning_projection_unavailable error_type=%s', type(error).__name__)
        blocks = []
    for block in blocks:
        if isinstance(block, dict) and block.get('type') == 'reasoning':
            text = block.get('reasoning')
            if isinstance(text, str):
                parts.append(text)
    if parts:
        return ''.join(parts)
    extension = message.additional_kwargs.get('reasoning_content')
    return extension if isinstance(extension, str) else ''
