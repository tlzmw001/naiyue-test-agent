"""Runtime binds fresh input identity; model access only projects content."""
import logging
from uuid import uuid4
from langchain_core.messages import AIMessage
from .context_calibration import KEY, measure_request, complete_sample
from langchain.agents.middleware import AgentMiddleware
from ..model_access.messages import prepare_model_messages

def _project_request(request):
    from .run_scope import current_run_scope
    scope = current_run_scope()
    return request.override(messages=prepare_model_messages(request.messages, request.model,
        fresh_run_id=scope.identity.run_id if scope else None))


class ModelInputMiddleware(AgentMiddleware):
    """No state update: overflow recovery outside sees the original request."""

    @property
    def name(self):
        return 'prepare_model_input'

    async def awrap_model_call(self, request, handler):
        projected = _project_request(request)
        sample_id = uuid4().hex
        measurement = measure_request(projected.model, projected.system_message,
            projected.messages, projected.tools, response_format=projected.response_format,
            calibration_history=request.messages)
        context = getattr(request.runtime, 'context', None)
        call = getattr(getattr(context, 'invocation', None), 'model_call', None)
        if call is not None:
            call.context_measurement = measurement
        response = await handler(projected)
        try:
            messages = [m for m in response.result if isinstance(m, AIMessage)]
            if len(messages) != 1:
                logging.getLogger(__name__).warning('calibration_response_not_unique')
            else:
                record = complete_sample(measurement, messages[0], sample_id=sample_id)
                if record is not None:
                    messages[0].response_metadata = {**messages[0].response_metadata, KEY: record}
        except Exception as error:
            logging.getLogger(__name__).warning('calibration_sample_failed error_type=%s', type(error).__name__)
        return response
