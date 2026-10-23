"""Explicit domain-shaped synchronous stubs for terminal presentation tests.

These do not test event-loop dispatch; the real bridge contract tests cover it.
No default capability or successful result is fabricated for absent domains.
"""
from types import SimpleNamespace


def portal(**domains):
    application = SimpleNamespace(**domains)
    return SimpleNamespace(call=lambda operation: operation(application))
