"""Explicit tool definitions and a process-local, ordered registry."""

from collections.abc import Sequence
from dataclasses import dataclass, field
import re
from threading import Lock
from typing import Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel
from .permissions import ToolPermission


@dataclass(frozen=True)
class ToolSpec:
    tool: BaseTool
    input_schema: type[BaseModel]
    execution_mode: Literal['parallel', 'sequential'] = 'parallel'
    side_effects: Literal['read_only', 'may_mutate', 'unknown'] = 'unknown'
    prompt_snippet: str = ''
    prompt_guidelines: tuple[str, ...] = ()
    permissions: object = field(default_factory=ToolPermission)

    def validate_input(self, args):
        # Runtime-injected fields are overwritten by ToolNode, including any
        # model-supplied value. Match that boundary before permission preparation.
        injected = set(self.tool.args_schema.model_fields) - set(self.input_schema.model_fields)
        return self.input_schema.model_validate({key: value for key, value in args.items() if key not in injected})


def _validate_input_schema(item: BaseTool, public: type[BaseModel]) -> None:
    execution = item.args_schema
    if (not isinstance(public, type) or not issubclass(public, BaseModel)
            or not isinstance(execution, type) or not issubclass(execution, public)):
        raise ValueError('Tool input schema must be the execution model or its public base')
    # The framework's exported model supplies field visibility only. Validation
    # rules remain on the original Pydantic models, never reconstructed from JSON.
    visible = set(item.tool_call_schema.model_fields)
    if 'runtime' in public.model_fields or set(public.model_fields) != visible:
        raise ValueError('Tool public input schema must exclude injected fields')
    for name, field in public.model_fields.items():
        if field.asdict() != execution.model_fields[name].asdict():
            raise ValueError('Tool input and execution schema fields must share validation')
    public_config = dict(public.model_config)
    execution_config = dict(execution.model_config)
    public_config.pop('arbitrary_types_allowed', None)
    execution_config.pop('arbitrary_types_allowed', None)
    if public_config != execution_config:
        raise ValueError('Tool input and execution schema configuration must match')
    for kind in ('validators', 'root_validators', 'field_validators', 'model_validators'):
        public_rules = getattr(public.__pydantic_decorators__, kind)
        execution_rules = getattr(execution.__pydantic_decorators__, kind)
        if public_rules.keys() != execution_rules.keys():
            raise ValueError('Tool input and execution schema validators must match')
        for name, rule in public_rules.items():
            other = execution_rules[name]
            if (getattr(rule.func, '__func__', rule.func) != getattr(other.func, '__func__', other.func)
                    or rule.info != other.info):
                raise ValueError('Tool input and execution schema validators must match')


def _validate_spec(spec: ToolSpec) -> None:
    if not isinstance(spec, ToolSpec):
        raise ValueError('Tool registration requires ToolSpec')
    item = spec.tool
    if not isinstance(item, BaseTool):
        raise ValueError('ToolSpec requires an original BaseTool')
    if not isinstance(item.name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]+', item.name):
        raise ValueError('Invalid tool name')
    if not isinstance(item.description, str) or not item.description.strip():
        raise ValueError('Tool description must not be empty')
    if spec.execution_mode not in ('parallel', 'sequential'):
        raise ValueError('Invalid tool execution mode')
    if spec.side_effects not in ('read_only', 'may_mutate', 'unknown'):
        raise ValueError('Invalid tool side effects')
    if (not isinstance(spec.prompt_snippet, str)
            or not isinstance(spec.prompt_guidelines, tuple)
            or any(not isinstance(value, str) for value in spec.prompt_guidelines)):
        raise ValueError('Invalid tool prompt metadata')
    if item.handle_tool_error:
        raise ValueError('Use ToolException for public feedback; handle_tool_error must be disabled.')
    if item.return_direct or item.response_format not in ('content', 'content_and_artifact'):
        raise ValueError('Unsupported tool result or routing contract')
    _validate_input_schema(item, spec.input_schema)
    for name in ('prepare', 'default_decision'):
        import inspect
        callback = getattr(spec.permissions, name, None)
        if not callable(callback) or inspect.iscoroutinefunction(callback):
            raise ValueError('Tool permission callbacks must be synchronous')
    path_field = getattr(spec.permissions, 'path_field', None)
    if path_field is not None and path_field not in spec.input_schema.model_fields:
        raise ValueError('File permission path field must be a public argument')


class ToolRegistry:
    """Validate structure, not stop/cleanup behavior; see docs/tool-authoring.md."""

    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}
        self._lock = Lock()

    def register(self, spec: ToolSpec, *, permission_settings=None) -> None:
        _validate_spec(spec)
        with self._lock:
            if spec.tool.name in self._specs:
                raise ValueError('Duplicate tool name in registration')
            if permission_settings is not None:
                from .permission_rules import validate_permission_rules
                validate_permission_rules(permission_settings, (*self._specs.values(), spec))
            self._specs[spec.tool.name] = spec

    def get(self, name: str) -> ToolSpec:
        with self._lock:
            if name not in self._specs:
                raise ValueError('Unknown tool name')
            return self._specs[name]

    def select(self, names: Sequence[str]) -> tuple[ToolSpec, ...]:
        if not isinstance(names, (list, tuple)) or any(not isinstance(name, str) for name in names):
            raise ValueError('Tool selection requires an ordered list of names')
        requested = tuple(names)
        if len(set(requested)) != len(requested):
            raise ValueError('Duplicate tool names in selection')
        with self._lock:
            if any(name not in self._specs for name in requested):
                raise ValueError('Unknown tool name in selection')
            return tuple(self._specs[name] for name in requested)

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._specs)
