"""Project message metadata shared by execution and bounded projections."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QueuedInput:
    message_id: str
    text: str
    kind: str


def reply_identity(message):
    value = message.additional_kwargs.get('naiyue_reply_id')
    return value if isinstance(value, str) and value else None
