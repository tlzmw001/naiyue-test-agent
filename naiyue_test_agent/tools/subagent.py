"""Immutable role definitions; execution is supplied by the application."""

from dataclasses import dataclass
import re

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .permissions import ToolPermission, PermissionMechanismError, require_tool_permit, permission_binding
from langchain_core.tools import ToolException
from .registry import ToolSpec
from .schemas import build_tool_schemas


@dataclass(frozen=True)
class SubAgentSpec:
    name: str
    description: str
    system_prompt: str
    tool_names: tuple[str, ...]
    model_id: str | None = None
    max_model_calls: int | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', self.name):
            raise ValueError('Invalid subagent name')
        for value in (self.description, self.system_prompt):
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Subagent description and system prompt must not be empty')
        if (not isinstance(self.tool_names, tuple)
                or any(not isinstance(name, str) or not name.strip() for name in self.tool_names)):
            raise ValueError('Subagent tools require a tuple of names')
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError('Duplicate subagent tool names')
        if {'subagent', 'subagent_list'}.intersection(self.tool_names):
            raise ValueError('Subagents cannot delegate to another subagent')
        if self.model_id is not None and (not isinstance(self.model_id, str) or not self.model_id.strip()):
            raise ValueError('Invalid subagent model_id')
        if self.max_model_calls is not None and (type(self.max_model_calls) is not int or self.max_model_calls <= 0):
            raise ValueError('Subagent max_model_calls must be a positive integer')


class SubAgentInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    agent: str = Field(min_length=1)
    task: str = Field(min_length=1)
    agent_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')

    @field_validator('agent', 'task')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('A non-empty agent and task are required')
        return value


def create_subagent_tool():
    """Built-in delegation tool; session selection controls whether it is active."""
    public, execution = build_tool_schemas('SubAgentInput', SubAgentInput)

    @tool('subagent', args_schema=execution, response_format='content_and_artifact')
    async def delegate(agent: str, task: str, runtime: ToolRuntime[dict, dict], agent_id: str | None = None):
        """Delegate a foreground task. Omit agent_id for fresh history, or pass a returned ID to continue."""
        require_tool_permit(runtime)
        port = runtime.context.get('ainvoke_subagent')
        if not callable(port):
            raise PermissionMechanismError('Subagent execution port is unavailable')
        return await port(agent, task, agent_id=agent_id, parent_context=runtime.context,
                          parent_state=runtime.state, parent_tool_call_id=runtime.tool_call_id)

    return ToolSpec(delegate, public, execution_mode='parallel', side_effects='unknown',
                    permissions=ToolPermission(default='allow'))


def subagent_selection(names):
    """Canonical user selection; the companion is derived per root request."""
    if not isinstance(names, (list, tuple)) or any(not isinstance(name, str) for name in names):
        raise ValueError('Tool selection requires an ordered list of names')
    if len(set(names)) != len(names):
        raise ValueError('Duplicate tool names in selection')
    if 'subagent_list' in names and 'subagent' not in names:
        raise ValueError('subagent_list requires the subagent capability')
    return tuple(name for name in names if name != 'subagent_list')


def subagent_request_tools(names, registry, *, agent_path='root'):
    names = tuple(name for name in names if name != 'subagent_list')
    if agent_path == 'root' and 'subagent' in names and 'subagent_list' in registry.names():
        return (*names, 'subagent_list')
    return names


class SubAgentListInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    limit: int | None = Field(default=None, gt=0)
    before: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


def create_subagent_list_tool():
    public, execution = build_tool_schemas('SubAgentListInput', SubAgentListInput)

    @tool('subagent_list', args_schema=execution)
    async def list_instances(runtime: ToolRuntime[dict, dict], limit: int | None = None, before: str | None = None):
        """Find this session's child instances and continuation candidates. Metadata only; paginate with next_before."""
        permit = require_tool_permit(runtime)
        if runtime.context.get('agent_path', 'root') != 'root':
            raise ToolException('Only the root agent can list subagent instances.')
        if permit.get('tool_name') != 'subagent_list':
            raise PermissionMechanismError('Subagent list permission does not match its tool')
        batch = runtime.state.get('tool_execution', {})
        call = next((call for call in batch.get('calls', [])
            if call['id'] == runtime.tool_call_id), None)
        if (call is None or permit != permission_binding(runtime.context, batch.get('batch_id'), call)
                or permit['subject'].get('args') != {'limit': limit, 'before': before}):
            raise PermissionMechanismError('Subagent list arguments do not match its execution permit')
        port = runtime.context.get('alist_subagents')
        if not callable(port):
            raise PermissionMechanismError('Subagent list execution port is unavailable')
        return await port(parent_context=runtime.context, parent_state=runtime.state, limit=limit, before=before)

    return ToolSpec(list_instances, public, execution_mode='parallel', side_effects='read_only',
                    permissions=ToolPermission(default='allow'))
