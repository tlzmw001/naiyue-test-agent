"""Best-effort observation boundaries; execution and control stay outside."""

import logging
import re
from collections.abc import Callable

from naiyue_test_agent.contracts.failures import safe_error_type


logger = logging.getLogger(__name__)


def observe(observer, operation: Callable, *args, fallback=None, diagnostic='observation_failed', **kwargs):
    """Execute a bound operation; ordinary body faults disable optional work.

    Bind methods at the call site so missing methods fail before this boundary.
    """
    if not callable(operation):
        raise TypeError('Observer operation must be callable')
    if observer is None or getattr(observer, '_observation_failed', False):
        return fallback
    operation_name = 'observer_operation'
    try:
        name = getattr(operation, '__name__', None)
        if isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', name):
            operation_name = name
        return operation(*args, **kwargs)
    except Exception as error:
        observer._observation_failed = True
        logger.error('%s operation=%s error_type=%s', diagnostic,
                     operation_name, safe_error_type(error))
        return fallback


def emit_event(publisher, event_type, payload):
    """Protect payload construction and notification.

    With a publisher, a payload factory is evaluated synchronously and
    immediately in this call. The factory is never retained or queued.
    """
    if publisher is None:
        return
    try:
        publisher.publish(event_type, payload() if callable(payload) else payload)
    except Exception as error:
        logger.error('run_event_publish_failed event_type=%s error_type=%s',
                     event_type, safe_error_type(error))
