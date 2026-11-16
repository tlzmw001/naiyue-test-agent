"""Shared memory query failure and exact condition fields."""

import re
from typing import Literal, TypedDict


class MemoryReadError(ValueError):
    pass


def safe_memory_reason(error):
    """Return the error's own fixed reason code when it is safe to expose."""
    reason = error.args[0] if error.args else None
    return reason if isinstance(reason, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', reason) else None


CONDITIONS = ('machine', 'module', 'target', 'subject')


class EvidenceUnit(TypedDict):
    unit_id: str
    evidence_id: str | None
    turn_key: str
    message_key: str
    sequence: int
    session_id: str
    thread_id: str
    message_id: str
    revision: int
    conversation_revision: int
    role: Literal["human", "ai", "tool"]
    reply_id: str | None
    parent_message_id: str | None
    tool_call_id: str | None
    name: str | None
    tool_status: str | None
    occurred_at: float
    user_origin: bool
    text: str
    start_offset: int
    end_offset: int
    text_chars: int
    origin: Literal["new", "overlap"]
    context_only: bool
    partial_before: bool
    partial_after: bool
    tool_pair_status: Literal["not_tool", "paired", "missing", "invalid"]


class MemoryBatch(TypedDict):
    units: list[EvidenceUnit]
    evidence_map: dict[str, EvidenceUnit]
    next_position: list[int]
    complete: bool
    history_version: int
    skipped: list[str]
    context_char_limit: int
    coverage: dict


class BoundProposal(TypedDict):
    proposal: dict
    evidence_ids: list[str]
    sources: list[dict]
    evidence: list[EvidenceUnit]
    context_units: list[EvidenceUnit]
    context_complete: bool
