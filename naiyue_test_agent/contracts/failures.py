"""Safe failure facts, not retry decisions or serializable exception objects."""

import inspect
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal


logger = logging.getLogger(__name__)
FailureSource = Literal['model', 'runtime', 'tool', 'application']
def safe_error_type(error: BaseException) -> str:
    name = type(error).__name__
    return name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', name) else 'Exception'


@dataclass(frozen=True)
class FailureDescription:
    source: FailureSource
    category: str
    reason: str
    error_type: str

    def __post_init__(self):
        if self.source not in ('model', 'runtime', 'tool', 'application'):
            raise ValueError('Invalid failure source')
        if any(not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', value)
               for value in (self.category, self.reason)):
            raise ValueError('Failure descriptions require fixed diagnostic codes')
        if (not isinstance(self.error_type, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', self.error_type)):
            raise ValueError('Invalid failure error type')


ErrorClassifier = Callable[[BaseException, FailureSource], FailureDescription | None]


def validate_failure_description(error, description, *, source=None):
    if (not isinstance(description, FailureDescription)
            or description.error_type != safe_error_type(error)
            or source is not None and description.source != source):
        raise ValueError('Invalid failure description')
    description.__post_init__()
    return description


def attach_failure_description(error, description, *, run_id=None):
    """Keep the exception itself as carrier; annotation faults are diagnostic."""
    try:
        validate_failure_description(error, description)
        if run_id is not None and (not isinstance(run_id, str) or not run_id):
            raise ValueError('Invalid failure annotation scope')
        error.naiyue_failure = (run_id, description)
        return True
    except Exception as fault:
        logger.error('failure_annotation_write_failed error_type=%s', safe_error_type(fault))
        return False


def read_failure_description(error, *, run_id=None):
    try:
        value = getattr(error, 'naiyue_failure', None)
        if value is None:
            return None
        if type(value) is not tuple or len(value) != 2 or value[0] != run_id:
            raise ValueError('Invalid failure annotation scope')
        return validate_failure_description(error, value[1])
    except Exception as fault:
        logger.error('failure_annotation_read_failed error_type=%s', safe_error_type(fault))
        return None


def normalize_classifiers(classifiers: Iterable[ErrorClassifier]) -> tuple[ErrorClassifier, ...]:
    result = []
    for item in classifiers:
        if (not callable(item) or inspect.iscoroutinefunction(item)
                or inspect.iscoroutinefunction(getattr(item, '__call__', None))):
            raise ValueError('Failure classifiers must be synchronous callables')
        try:
            signature = inspect.signature(item)
        except (TypeError, ValueError):
            raise ValueError('Failure classifiers require an inspectable signature') from None
        try:
            signature.bind(None, 'model')
        except TypeError:
            raise ValueError('Failure classifiers must accept error and source') from None
        result.append(item)
    return tuple(result)


def resolve_failure(error: BaseException, *, source: FailureSource, classifiers,
                    builtin, report_diagnostic, fallback=True) -> FailureDescription | None:
    """Choose the first matching description; diagnostic faults preserve the input error."""
    error_type = safe_error_type(error)
    try:
        for classify in normalize_classifiers(classifiers):
            result = classify(error, source)
            if result is None:
                continue
            if inspect.iscoroutine(result):
                result.close()
            return validate_failure_description(error, result, source=source)
        classified = builtin(error)
        if classified is None and not fallback:
            return None
        category, reason = classified if classified is not None else ('unknown', 'unclassified')
        return FailureDescription(source, category, reason, error_type)
    except Exception as diagnostic_error:
        try:
            report_diagnostic(diagnostic_error)
        except Exception as report_error:
            logger.error('failure_diagnostic_report_failed error_type=%s', safe_error_type(report_error))
        return FailureDescription(source, 'unknown', 'unclassified', error_type)


@dataclass(frozen=True)
class RunFailureData:
    stage: Literal['prepare', 'execute', 'finalize', 'cleanup']
    category: str
    reason: str
    error_type: str

    def __post_init__(self):
        if self.stage not in ('prepare', 'execute', 'finalize', 'cleanup'):
            raise ValueError('Invalid run failure stage')
        if any(not isinstance(value, str)
               or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', value)
               for value in (self.category, self.reason)):
            raise ValueError('Run failure requires fixed diagnostic codes')
        if (not isinstance(self.error_type, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', self.error_type)):
            raise ValueError('Invalid run failure error type')
