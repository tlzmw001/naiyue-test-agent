"""Public tool feedback at the execution boundary, without replaying actions."""

import logging

from langchain_core.tools import ToolException

from naiyue_test_agent.contracts.failures import safe_error_type
from ..call_observation import event_label
from ..errors import ContextCompactionError, ModelResponseRejected, ToolBatchSchedulingError
from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.tools.permissions import PermissionMechanismError


logger = logging.getLogger(__name__)


def tool_error_feedback(error, request):
    """Return model-visible content, or None to preserve a mechanism failure."""
    if isinstance(error, (ProcessCleanupError, ToolBatchSchedulingError,
                          ModelResponseRejected, ContextCompactionError, PermissionMechanismError)):
        return None
    if isinstance(error, ToolException):
        content = str(error)
    else:
        content = (
            f'Tool execution failed ({safe_error_type(error)}). '
            'No normal result is available. '
            'Use the available context to decide the next step.'
        )
    try:
        context = request.runtime.context or {}
        logger.warning('tool_error_converted error_type=%s', safe_error_type(error), extra={'tool_error': {
            'event': 'tool_error_converted',
            'tool_name': event_label(request.tool_call['name']),
            'tool_call_id': event_label(request.tool_call['id']),
            'session_id': event_label(context.get('session_id')),
            'run_id': event_label(context.get('run_id')),
            'error_type': safe_error_type(error),
            'cause_type': safe_error_type(error.__cause__) if error.__cause__ is not None else None,
        }})
    except Exception as fault:
        logger.error('tool_error_diagnostics_failed error_type=%s', safe_error_type(fault))
    return content
