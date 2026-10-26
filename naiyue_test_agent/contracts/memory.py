"""Shared memory query failure and exact condition fields."""


class MemoryReadError(ValueError):
    pass


CONDITIONS = ('machine', 'module', 'target', 'subject')
