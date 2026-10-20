"""Consume an approval once, then pass a per-call permit to trusted tools."""

from dataclasses import replace

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from naiyue_test_agent.tools.permissions import OneUseApprovalGrants, PermissionMechanismError, permission_binding


class ExecutionAuthorizationMiddleware(AgentMiddleware):
    def __init__(self, settings):
        self.settings = settings

    def _authorize(self, request):
        batch = request.state.get('tool_execution', {})
        call = next((item for item in batch.get('calls', []) if item['id'] == request.tool_call['id']), None)
        if (call is None or call['name'] != request.tool_call['name'] or call['args'] != request.tool_call['args']):
            raise PermissionMechanismError('Tool call does not match its prepared permission')
        if call['validation'] == 'invalid':
            if isinstance(call.get('validation_feedback'), str):
                return ToolMessage(content=call['validation_feedback'], tool_call_id=call['id'],
                    name=call['name'], status='error', additional_kwargs={'naiyue_not_executed': 'invalid_arguments'})
            return request
        if call['validation'] == 'target_error':
            raise ToolException(call['feedback'])
        decision = call['decision']
        if decision == 'deny':
            return ToolMessage(content='Tool was not executed: access denied by the permission policy (' + call['source'] + ').',
                tool_call_id=call['id'], name=call['name'], status='error',
                additional_kwargs={'naiyue_not_executed': 'permission_denied'})
        context = request.runtime.context
        binding = permission_binding(context, batch['batch_id'], call)
        if decision == 'ask':
            grants = context.get('approval_grants')
            if not isinstance(grants, OneUseApprovalGrants) or not grants.consume(call['id'], binding):
                raise PermissionMechanismError('One-use approval is missing or does not match this call')
        elif decision != 'allow':
            raise PermissionMechanismError('Invalid saved permission decision')
        local = {**context, '_execution_permit': binding, '_permission_rules': self.settings,
                 '_session_allow': batch.get('session_allow', []), '_permission_mode': batch['mode']}
        return request.override(runtime=replace(request.runtime, context=local))


    async def awrap_tool_call(self, request, handler):
        authorized = self._authorize(request)
        return authorized if isinstance(authorized, ToolMessage) else await handler(authorized)
