"""Transient text deltas and tool progress, never model state updates."""

from dataclasses import dataclass
from typing import Literal


SUMMARY_TAG = 'naiyue_summary'
PERMISSION_REVIEW_TAG = 'naiyue_permission_review'


@dataclass(frozen=True)
class MessageDeltaData:
    message_id: str
    text: str
    index: int
    provisional: Literal[True] = True

    def __post_init__(self):
        if not isinstance(self.message_id, str) or not self.message_id:
            raise ValueError('Delta requires a message ID')
        if not isinstance(self.text, str) or not self.text:
            raise ValueError('Delta requires text')
        if type(self.index) is not int or self.index <= 0 or self.provisional is not True:
            raise ValueError('Invalid provisional delta')


@dataclass(frozen=True)
class ToolProgressData:
    batch_id: str
    tool_call_id: str
    tool_name: str
    activity: Literal['queued', 'preparing', 'running', 'stopping', 'settling']
    phase: str
    completed: int | None = None
    total: int | None = None
    unit: Literal['bytes', 'lines', 'entries', 'matches'] | None = None
    text: str = ''

    def __post_init__(self):
        if (any(not isinstance(v, str) or not v for v in (self.batch_id, self.tool_call_id, self.tool_name))
                or self.activity not in ('queued', 'preparing', 'running', 'stopping', 'settling')
                or not isinstance(self.phase, str) or not 0 < len(self.phase) <= 64
                or self.unit not in (None, 'bytes', 'lines', 'entries', 'matches')
                or not isinstance(self.text, str)
                or any(v is not None and (type(v) is not int or v < 0) for v in (self.completed, self.total))
                or self.completed is not None and self.total is not None and self.completed > self.total):
            raise ValueError('Invalid tool progress')
