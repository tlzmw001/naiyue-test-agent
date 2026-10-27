"""Runtime binds fresh input identity; model access only projects content."""
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

    def wrap_model_call(self, request, handler):
        return handler(_project_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(_project_request(request))
