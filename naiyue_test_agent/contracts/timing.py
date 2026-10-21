"""Optional monotonic observation shared by the three terminal fact producers."""

import logging
from time import monotonic_ns

from .failures import safe_error_type


logger = logging.getLogger(__name__)


def validate_duration(value):
    if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
        raise ValueError('Invalid duration')
    return value


def start_timer():
    try:
        value = monotonic_ns()
        if type(value) is not int or value < 0:
            raise ValueError('Invalid monotonic time')
        return value
    except Exception as error:
        logger.warning('duration_observation_failed phase=start error_type=%s', safe_error_type(error))
        return None


def elapsed_ms(started):
    if started is None:
        return None
    try:
        now = monotonic_ns()
        if type(now) is not int or type(started) is not int or not 0 <= started <= now:
            raise ValueError('Invalid monotonic interval')
        return validate_duration((now - started) // 1_000_000)
    except Exception as error:
        logger.warning('duration_observation_failed phase=finish error_type=%s', safe_error_type(error))
        return None
