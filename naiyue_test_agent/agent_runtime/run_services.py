"""One scoped bridge for run dependencies; never stores application objects."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .events import RunEventPublisher
    from .errors import RuntimeErrorPolicy
    from .run_control import RunControl
    from .turn_observation import TurnObserver
    from .control_observation import ControlObserver
    from .context_operation_observation import ContextOperationObserver


@dataclass(frozen=True)
class RunServices:
    publisher: 'RunEventPublisher | None' = None
    errors: 'RuntimeErrorPolicy | None' = None
    control: 'RunControl | None' = None
    turns: 'TurnObserver | None' = None
    controls: 'ControlObserver | None' = None
    context_operations: 'ContextOperationObserver | None' = None


_RUN_SERVICES = ContextVar('naiyue_run_services', default=None)


@contextmanager
def bind_run_services(services):
    token = _RUN_SERVICES.set(services)
    try:
        yield
    finally:
        _RUN_SERVICES.reset(token)


def current_run_services():
    return _RUN_SERVICES.get()
