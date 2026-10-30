"""Shared memory query failure and exact condition fields."""

import re


class MemoryReadError(ValueError):
    pass


def safe_memory_reason(error):
    """Return the error's own fixed reason code when it is safe to expose."""
    reason = error.args[0] if error.args else None
    return reason if isinstance(reason, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', reason) else None


CONDITIONS = ('machine', 'module', 'target', 'subject')
