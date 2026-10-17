"""Shared run/control facts produced by the application and runtime observers."""

from dataclasses import dataclass
from typing import Literal

from .failures import RunFailureData


@dataclass(frozen=True)
class RunStartedData:
    mode: Literal['message', 'approval_resume']

    def __post_init__(self):
        if self.mode not in ('message', 'approval_resume'):
            raise ValueError('Invalid run start mode')



@dataclass(frozen=True)
class RunFinishedData:
    status: Literal['completed', 'stopped', 'interrupted', 'failed']
    stop_mode: Literal['active'] | None = None
    manual_review_required: bool = False
    failure: RunFailureData | None = None

    def __post_init__(self):
        if self.status not in ('completed', 'stopped', 'interrupted', 'failed'):
            raise ValueError('Invalid run terminal status')
        if self.stop_mode != ('active' if self.status == 'stopped' else None):
            raise ValueError('Invalid run stop mode')
        if type(self.manual_review_required) is not bool:
            raise ValueError('Invalid manual review flag')
        if (self.status == 'failed') != isinstance(self.failure, RunFailureData):
            raise ValueError('Invalid run failure details')



@dataclass(frozen=True)
class ControlData:
    control_id: str
    kind: Literal['steering', 'follow_up', 'stop', 'approval']
    boundary: str = 'application'
    message_id: str | None = None
    visibility: Literal['graph_values', 'recovery_view'] | None = None
    tool_call_ids: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    origin_run_id: str | None = None
    reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.control_id, str) or not self.control_id:
            raise ValueError('A control identifier is required')
        if self.kind not in ('steering', 'follow_up', 'stop', 'approval'):
            raise ValueError('Invalid control kind')
        if self.boundary not in ('application', 'queue', 'message_state', 'active',
                                 'invocation', 'approval_hook'):
            raise ValueError('Invalid control boundary')
        if any(v is not None and (not isinstance(v, str) or not v)
               for v in (self.message_id, self.origin_run_id)):
            raise ValueError('Invalid control correlation identifier')
        if self.visibility not in (None, 'graph_values', 'recovery_view'):
            raise ValueError('Invalid control visibility')
        if type(self.tool_call_ids) is not tuple or any(not isinstance(v, str) or not v for v in self.tool_call_ids):
            raise ValueError('Immutable tool identifiers are required')
        if type(self.decisions) is not tuple or any(v not in ('approve', 'reject') for v in self.decisions):
            raise ValueError('Invalid approval decisions')
        if self.kind != 'approval' and (self.tool_call_ids or self.decisions or self.origin_run_id):
            raise ValueError('Approval details require an approval control')
        if self.reason not in (None, 'run_completed', 'run_stopped', 'run_interrupted', 'run_failed',
                               'prepare_failed', 'control_exit', 'message_state_unconfirmed',
                               'approval_resolution_unconfirmed'):
            raise ValueError('Invalid control outcome reason')

    @property
    def approved_count(self):
        return self.decisions.count('approve')

    @property
    def rejected_count(self):
        return self.decisions.count('reject')

    @property
    def stop_mode(self):
        return 'active' if self.kind == 'stop' else None


@dataclass(frozen=True)
class ApprovalRequestedData:
    tool_call_ids: tuple[str, ...]

    def __post_init__(self):
        if (type(self.tool_call_ids) is not tuple or not self.tool_call_ids
                or any(not isinstance(v, str) or not v for v in self.tool_call_ids)):
            raise ValueError('Actual pending tool identifiers are required')

    @property
    def count(self):
        return len(self.tool_call_ids)


SESSION_PAYLOADS = {
    'run_started': RunStartedData, 'run_finished': RunFinishedData,
    'control_accepted': ControlData, 'control_delivery_started': ControlData,
    'control_delivered': ControlData, 'control_applied': ControlData,
    'control_discarded': ControlData, 'control_outcome_unknown': ControlData,
    'approval_requested': ApprovalRequestedData,
}
