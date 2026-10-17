"""Finite, immutable turn metadata; content remains in message events."""

from dataclasses import dataclass
from typing import Literal

from naiyue_test_agent.contracts.failures import RunFailureData


def _identity(turn_id, origin_run_id, model_call_id):
    if any(not isinstance(v, str) or not v for v in (turn_id, origin_run_id)):
        raise ValueError('Turn and origin identifiers are required')
    if model_call_id is not None and (not isinstance(model_call_id, str) or not model_call_id):
        raise ValueError('Invalid logical model call identifier')


@dataclass(frozen=True)
class TurnStartedData:
    turn_id: str
    origin_run_id: str
    model_call_id: str | None
    kind: Literal['model', 'approval_resume', 'approval_retry'] = 'model'
    previous_turn_id: str | None = None

    def __post_init__(self):
        _identity(self.turn_id, self.origin_run_id, self.model_call_id)
        if self.kind not in ('model', 'approval_resume', 'approval_retry'):
            raise ValueError('Invalid turn start kind')
        if self.kind == 'model' and self.model_call_id is None:
            raise ValueError('A model turn must reference its logical call')
        if self.previous_turn_id is not None and (
            self.kind != 'approval_retry' or not isinstance(self.previous_turn_id, str)
            or not self.previous_turn_id or self.previous_turn_id == self.turn_id
        ):
            raise ValueError('Invalid failed-turn reference')


@dataclass(frozen=True)
class TurnFinishedData:
    turn_id: str
    origin_run_id: str
    model_call_id: str | None
    status: Literal['completed', 'suspended', 'failed']
    assistant_message_id: str | None = None
    tool_call_ids: tuple[str, ...] = ()
    tool_message_ids: tuple[str, ...] = ()
    error_result_count: int = 0
    failure: RunFailureData | None = None

    def __post_init__(self):
        _identity(self.turn_id, self.origin_run_id, self.model_call_id)
        if self.status not in ('completed', 'suspended', 'failed'):
            raise ValueError('Invalid turn outcome')
        if (self.status == 'failed') != isinstance(self.failure, RunFailureData):
            raise ValueError('Invalid turn failure details')
        if self.assistant_message_id is not None and (
            not isinstance(self.assistant_message_id, str) or not self.assistant_message_id
        ):
            raise ValueError('Invalid assistant message identifier')
        for ids in (self.tool_call_ids, self.tool_message_ids):
            if type(ids) is not tuple or any(not isinstance(v, str) or not v for v in ids):
                raise ValueError('Immutable tool identifiers are required')
        if type(self.error_result_count) is not int or not 0 <= self.error_result_count <= len(self.tool_message_ids):
            raise ValueError('Invalid error result count')
        if self.status == 'completed' and (
            self.assistant_message_id is None or len(self.tool_call_ids) != len(self.tool_message_ids)
        ):
            raise ValueError('A complete turn requires its actual message results')


@dataclass(frozen=True)
class TurnUnavailableData:
    turn_id: str | None
    reason: Literal['state_observation_unavailable', 'incomplete_turn',
                    'missing_approval_context', 'unbound_tool_call', 'ambiguous_tool_results']

    def __post_init__(self):
        if self.turn_id is not None and (not isinstance(self.turn_id, str) or not self.turn_id):
            raise ValueError('Invalid turn identifier')
        if self.reason not in ('state_observation_unavailable', 'incomplete_turn',
                               'missing_approval_context', 'unbound_tool_call', 'ambiguous_tool_results'):
            raise ValueError('Invalid turn observation reason')
