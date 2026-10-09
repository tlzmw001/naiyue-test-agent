"""Provisional text deltas are presentation data, never model state updates."""

from dataclasses import dataclass
from typing import Literal


SUMMARY_TAG = 'naiyue_summary'


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
