"""Four context-operation types with finite metadata and no prompt content."""

import re
from dataclasses import dataclass


REASONS = frozenset({
    'unsupported_summary_content', 'ambiguous_tool_call_id', 'incomplete_tool_pair',
    'no_new_history_to_summarize', 'no_safe_cut', 'no_safe_cut_within_send_budget',
    'summary_input_exceeds_budget', 'summary_failed', 'summary_invalid',
    'compacted_request_over_budget', 'compaction_not_reducing', 'compaction_internal',
    'compaction_unavailable', 'state_replacement_unconfirmed', 'retry_response_unconfirmed',
    'invalid_tool_calls', 'missing_ai_message', 'missing_tool_calls', 'stop_reason_error',
    'timeout', 'connection_error', 'rate_limit', 'context_window_exceeded', 'unclassified',
})


def safe_context_reason(reason):
    return reason if isinstance(reason, str) and reason in REASONS else 'unclassified'


@dataclass(frozen=True)
class ContextOperationStartedData:
    operation_id: str
    operation: str
    model_id: str | None
    trigger: str
    model_call_id: str | None = None
    before_estimate: int | None = None
    original_message_count: int | None = None

    def __post_init__(self):
        if not isinstance(self.operation_id, str) or not self.operation_id:
            raise ValueError('A context operation identifier is required')
        if self.operation not in ('compaction', 'recovery') or self.trigger not in ('threshold', 'overflow', 'manual'):
            raise ValueError('Invalid context operation')
        if self.operation == 'recovery' and self.trigger != 'overflow':
            raise ValueError('Recovery requires explicit overflow')
        if self.model_id is not None and (not isinstance(self.model_id, str)
                                          or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', self.model_id)):
            raise ValueError('Invalid model label')
        if self.model_call_id is not None and (not isinstance(self.model_call_id, str) or not self.model_call_id):
            raise ValueError('Invalid logical model call reference')
        if any(v is not None and (type(v) is not int or v < 0)
               for v in (self.before_estimate, self.original_message_count)):
            raise ValueError('Invalid context estimate')


@dataclass(frozen=True)
class ContextOperationFinishedData(ContextOperationStartedData):
    status: str = 'unconfirmed'
    stage: str = 'state'
    reason: str | None = None
    error_type: str | None = None
    after_estimate: int | None = None
    removed_message_count: int | None = None
    preserved_message_count: int | None = None
    summary_input_tokens: int | None = None
    summary_output_tokens: int | None = None
    summary_duration_ms: int | None = None
    summary_message_id: str | None = None
    visibility: str | None = None
    related_operation_id: str | None = None
    retry_attempts: int = 0
    summary_cache_read_tokens: int | None = None
    summary_cache_write_tokens: int | None = None

    def __post_init__(self):
        super().__post_init__()
        allowed = (('applied', 'skipped', 'fallback', 'failed', 'unconfirmed') if self.operation == 'compaction'
                   else ('recovered', 'failed', 'unconfirmed'))
        if self.status not in allowed or self.stage not in ('cut', 'summary', 'compact', 'retry', 'response_validation', 'state'):
            raise ValueError('Invalid context operation outcome')
        if self.reason is not None and self.reason not in REASONS:
            raise ValueError('A fixed context reason is required')
        if self.error_type is not None and (not isinstance(self.error_type, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', self.error_type)):
            raise ValueError('Invalid context error type')
        if any(v is not None and (type(v) is not int or v < 0) for v in (
            self.after_estimate, self.removed_message_count, self.preserved_message_count,
            self.summary_input_tokens, self.summary_output_tokens, self.summary_duration_ms,
            self.summary_cache_read_tokens, self.summary_cache_write_tokens,
        )):
            raise ValueError('Invalid context measurements')
        if type(self.retry_attempts) is not int or self.retry_attempts not in (0, 1):
            raise ValueError('Invalid recovery attempt count')
        if self.visibility not in (None, 'graph_values', 'recovery_view'):
            raise ValueError('Invalid context state visibility')
        if any(v is not None and (not isinstance(v, str) or not v)
               for v in (self.summary_message_id, self.related_operation_id)):
            raise ValueError('Invalid context state reference')
        if self.status in ('applied', 'recovered') and (self.summary_message_id is None or self.visibility is None):
            raise ValueError('A successful context operation requires a readable replacement')
