"""The single scope bridge for callbacks without a framework runtime argument."""

from contextlib import contextmanager
from contextvars import ContextVar

from ..contracts.run_context import RunScope


_RUN_SCOPE: ContextVar[RunScope | None] = ContextVar('naiyue_run_scope', default=None)


@contextmanager
def bind_run_scope(scope: RunScope):
    token = _RUN_SCOPE.set(scope)
    try:
        yield
    finally:
        _RUN_SCOPE.reset(token)


def current_run_scope() -> RunScope | None:
    return _RUN_SCOPE.get()
