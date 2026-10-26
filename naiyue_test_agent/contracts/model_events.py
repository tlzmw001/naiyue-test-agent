"""Invocation facts shared by execution and persistent usage."""

from dataclasses import dataclass
import re
from typing import Literal
from .timing import validate_duration


def check_call_subject(call_id, label):
    if not isinstance(call_id, str) or not call_id:
        raise ValueError('A call identifier is required')
    if label is not None and (not isinstance(label, str)
                              or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', label)):
        raise ValueError('Invalid call label')


@dataclass(frozen=True)
class ModelInvocationStartedData:
    invocation_id: str
    model_id: str | None
    purpose: str | None

    def __post_init__(self):
        check_call_subject(self.invocation_id, self.model_id)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.invocation_id):
            raise ValueError('Invalid invocation identifier')
        if self.purpose not in (None, 'agent', 'summary', 'auto_fast', 'auto_detailed', 'memory'):
            raise ValueError('Invalid invocation purpose')


@dataclass(frozen=True)
class ModelInvocationFinishedData:
    invocation_id: str
    status: str
    usage_status: str = 'unavailable'
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    error_type: str | None = None
    duration_ms: int | None = None
    http_capture_status: Literal['complete', 'incomplete', 'unavailable'] | None = None

    def __post_init__(self):
        validate_duration(self.duration_ms)
        if self.http_capture_status not in (None, 'complete', 'incomplete', 'unavailable'):
            raise ValueError('Invalid HTTP capture status')
        check_call_subject(self.invocation_id, None)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.invocation_id):
            raise ValueError('Invalid invocation identifier')
        if self.status not in ('returned', 'failed', 'cancelled'):
            raise ValueError('Invalid invocation outcome')
        if self.usage_status not in ('final', 'partial', 'unavailable'):
            raise ValueError('Invalid invocation usage status')
        values = (self.input_tokens, self.output_tokens, self.cache_read_tokens, self.cache_write_tokens)
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
            raise ValueError('Invalid invocation usage')
        if ((self.usage_status == 'unavailable') != all(value is None for value in values)
                or self.usage_status == 'final' and self.status != 'returned'
                or self.usage_status == 'partial' and self.status == 'returned'):
            raise ValueError('Invocation usage does not match outcome')
        if self.error_type is not None and (self.status == 'returned' or not isinstance(self.error_type, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', self.error_type)):
            raise ValueError('Invalid invocation error type')
