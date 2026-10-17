"""Immutable role definitions; execution is supplied by the application."""

from dataclasses import dataclass
import re

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .permissions import ToolPermission, PermissionMechanismError, require_tool_permit
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
        if 'subagent' in self.tool_names:
            raise ValueError('Subagents cannot delegate to another subagent')
        if self.model_id is not None and (not isinstance(self.model_id, str) or not self.model_id.strip()):
            raise ValueError('Invalid subagent model_id')
        if self.max_model_calls is not None and (type(self.max_model_calls) is not int or self.max_model_calls <= 0):
            raise ValueError('Subagent max_model_calls must be a positive integer')


class SubAgentInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    agent: str = Field(min_length=1)
    task: str = Field(min_length=1)

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
    async def delegate(agent: str, task: str, runtime: ToolRuntime[dict, dict]):
        """Delegate an independent foreground task to a registered subagent role."""
        require_tool_permit(runtime)
        port = runtime.context.get('ainvoke_subagent')
        if not callable(port):
            raise PermissionMechanismError('Subagent execution port is unavailable')
        return await port(agent, task, parent_context=runtime.context,
                          parent_state=runtime.state, parent_tool_call_id=runtime.tool_call_id)

    return ToolSpec(delegate, public, execution_mode='parallel', side_effects='unknown',
                    permissions=ToolPermission(default='allow'))
