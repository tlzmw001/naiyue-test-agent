"""Add application-selected project instructions to one model request."""

from collections.abc import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage


def make_project_prompt_middleware(
    resolve_instructions: Callable[[str], str | None],
):
    """Keep workspace file selection out of the generic agent runtime."""

    return _ProjectPromptMiddleware(resolve_instructions)


class _ProjectPromptMiddleware(AgentMiddleware):
    def __init__(self, resolve_instructions):
        self.resolve_instructions = resolve_instructions

    @property
    def name(self):
        return 'add_project_instructions'

    def _prepare_request(self, request):
        session_id = request.runtime.context['session_id']
        instructions = self.resolve_instructions(session_id)
        if not instructions:
            return request
        base = request.system_message.content if request.system_message else ''
        if not isinstance(base, str):
            raise TypeError('A text system message is required for project instructions')
        system_message = SystemMessage(content=base + instructions)
        return request.override(system_message=system_message)

    def wrap_model_call(self, request: ModelRequest, handler):
        return handler(self._prepare_request(request))

    async def awrap_model_call(self, request: ModelRequest, handler):
        return await handler(self._prepare_request(request))
