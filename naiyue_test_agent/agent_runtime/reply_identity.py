"""Attach ownership before the model result is first written to the graph."""

from dataclasses import replace
from typing import NotRequired
from langchain.agents.middleware import AgentMiddleware, AgentState
from naiyue_test_agent.contracts.replies import reply_identity


class ReplyState(AgentState):
    active_reply_id: NotRequired[str | None]


class ReplyIdentityMiddleware(AgentMiddleware):
    state_schema = ReplyState

    async def awrap_model_call(self, request, handler):
        identity = next((reply_identity(m) for m in reversed(request.state['messages']) if reply_identity(m)), None)
        response = await handler(request)
        if identity is None:
            return response
        return replace(response, result=[message.model_copy(update={'additional_kwargs': {
            **message.additional_kwargs, 'naiyue_reply_id': identity}}) for message in response.result])
