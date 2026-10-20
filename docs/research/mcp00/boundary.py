"""Research-only JSON Schema branch; production ToolRegistry is NOT changed."""
import json
from typing import TypedDict

from clients import PROJECT
import sys
sys.path.insert(0, str(PROJECT / 'tests'))
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from jsonschema import Draft202012Validator
from pydantic import RootModel, ValidationError
from naiyue_test_agent.tools.permissions import ToolPermission, require_tool_permit
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.agent_runtime.tool_execution.permissions import prepare_permission_call
from naiyue_test_agent.agent_runtime.tool_execution.authorization import ExecutionAuthorizationMiddleware


class SchemaSpec:
    def __init__(self, tool, schema, decision='ask'):
        Draft202012Validator.check_schema(schema)
        def check_refs(node):
            if isinstance(node, dict):
                if '$ref' in node and not node['$ref'].startswith('#'):
                    raise ValueError('external_ref_not_supported')
                for value in node.values():
                    check_refs(value)
            elif isinstance(node, list):
                for value in node:
                    check_refs(value)
        check_refs(schema)
        if 'runtime' in schema.get('properties', {}):
            raise ValueError('reserved_runtime_parameter')
        self.tool = tool
        self.validator = Draft202012Validator(schema)
        self.permissions = ToolPermission(default=decision)
        self.side_effects = True

    def validate_input(self, arguments):
        error = next(self.validator.iter_errors(arguments), None)
        if error is not None:
            # Exercise the CURRENT project's ValidationError boundary, not a new
            # production decision. No rejected value goes into error diagnostics.
            raise ValidationError.from_exception_data('ResearchMcpInput', [{
                'type': 'value_error', 'loc': tuple(error.absolute_path), 'input': None,
                'ctx': {'error': ValueError('invalid_mcp_arguments')}}])
        return RootModel[dict](arguments)


class State(TypedDict):
    messages: list
    tool_execution: dict


def tool_boundary(owner, schema, decision='ask'):
    async def execute(runtime: ToolRuntime, **arguments):
        permit = require_tool_permit(runtime)
        assert permit['subject']['args'] == arguments
        result = await owner.call('echo', arguments)
        return json.dumps(result['structured'])

    tool = StructuredTool(name='mcp_fixture_echo', description='Research generic MCP tool',
                          args_schema=schema, coroutine=execute)
    spec = SchemaSpec(tool, schema, decision)
    settings = PermissionSettings()
    authorization = ExecutionAuthorizationMiddleware(settings)

    async def authorize(request, handler):
        prepared = request.state['tool_execution']['calls'][0]
        if prepared['validation'] == 'invalid':
            # This is a REQUIRED MCP-01 production change, simulated only here.
            return ToolMessage(content='Invalid MCP arguments; not executed', status='error',
                               tool_call_id=request.tool_call['id'], name=request.tool_call['name'])
        return await authorization.awrap_tool_call(request, handler)

    builder = StateGraph(State, context_schema=dict)
    builder.add_node('tools', ToolNode([tool], awrap_tool_call=authorize))
    builder.add_edge(START, 'tools')
    builder.add_edge('tools', END)
    return spec, settings, builder.compile()


def prepared_input(spec, settings, arguments, context):
    call = {'name': spec.tool.name, 'args': arguments, 'id': 'outer-call'}
    prepared = prepare_permission_call(call, spec, context, settings, [])
    batch = {'batch_id': 'batch-1', 'mode': 'Default', 'session_allow': [], 'calls': [prepared]}
    return {'messages': [AIMessage(content='', tool_calls=[call])], 'tool_execution': batch}, prepared
