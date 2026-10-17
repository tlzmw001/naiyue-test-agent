"""Assembly and model routing for the LangChain agent loop."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ToolErrorMiddleware, after_agent, before_agent, before_model,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from naiyue_test_agent.agent_runtime.tool_execution.batch import ToolBatchMiddleware
from naiyue_test_agent.agent_runtime.context_observation import observe_model_request
from naiyue_test_agent.agent_runtime.context_compaction import (
    make_context_compaction_middleware,
)
from naiyue_test_agent.agent_runtime.context_recovery import recover_input_overflow
from .tool_execution.toolset import ToolSetMiddleware, ToolExecutionState
from naiyue_test_agent.tools.permissions import (
    OneUseApprovalGrants,
)
from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.model_access.messages import ModelInputMiddleware
from .errors import RuntimeErrorPolicy
from naiyue_test_agent.tools.registry import ToolRegistry


from naiyue_test_agent.agent_runtime.call_observation import (
    make_model_selection_middleware,
)
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, AgentEventPublisher
from .active_stop import StopModelMiddleware, StopBoundaryMiddleware
from .run_control import RunControl
from naiyue_test_agent.agent_runtime.turn_observation import (
    TurnBoundaryMiddleware, TurnObserver,
)
from naiyue_test_agent.agent_runtime.control_observation import ControlObserver
from .tool_execution import tool_error_feedback
from .tool_execution.observation import _record_tool_call
from .tool_execution.approval import BatchApprovalMiddleware
from .tool_execution.permissions import AutoPermissionReviewer, PermissionMiddleware, fixed_permission_context
from .tool_execution.authorization import ExecutionAuthorizationMiddleware
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.agent_runtime.context_operation_observation import (
    ContextOperationObserver,
)


class RunContext(TypedDict):
    session_id: str
    agent_path: NotRequired[str]
    parent_tool_call_id: NotRequired[str]
    parent_run_id: NotRequired[str]
    root_run_id: NotRequired[str]
    ainvoke_subagent: NotRequired[Callable]
    run_id: NotRequired[str]
    workspace_root: NotRequired[Path]
    approval_grants: NotRequired[OneUseApprovalGrants]
    read_resource_roots: NotRequired[tuple[Path, ...]]
    _run_control: NotRequired[RunControl]
    _run_event_publisher: NotRequired[RunEventPublisher | AgentEventPublisher]
    _turn_observer: NotRequired[TurnObserver]
    _control_observer: NotRequired[ControlObserver]
    _context_observer: NotRequired[ContextOperationObserver]
    _failure_policy: NotRequired[RuntimeErrorPolicy]


class InteractiveRunState(ToolExecutionState):
    run_stopped: NotRequired[bool]


def build_runtime_agent(
    default_model: BaseChatModel,
    *,
    tool_registry: ToolRegistry,
    resolve_active_tool_names: Callable,
    system_prompt: str,
    response_format: Any,
    resolve_model: Callable[[str, str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None = None,
    permission_settings: PermissionSettings | None = None,
    resolve_permission_context: Callable = fixed_permission_context,
    permission_reviewer: AutoPermissionReviewer | None = None,
):
    """Own LangChain agent assembly while callers supply task-specific policy."""
    return _build_runtime_agent(
        default_model, tool_registry=tool_registry, resolve_active_tool_names=resolve_active_tool_names, system_prompt=system_prompt,
        response_format=response_format, resolve_model=resolve_model,
        record_selection=record_selection, checkpointer=checkpointer,
        steering_middleware=[], permission_settings=permission_settings, resolve_permission_context=resolve_permission_context,
        permission_reviewer=permission_reviewer,
    )


def _assemble_interactive_runtime(
    default_model: BaseChatModel,
    *,
    tool_registry: ToolRegistry,
    resolve_active_tool_names: Callable,
    system_prompt: str,
    response_format: Any,
    resolve_model: Callable[[str, str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None,
    drain_steering: Callable[[str], list[str]],
    is_stop_requested: Callable[[str], bool],
    finish_run_boundary: Callable[[str], tuple[bool, list[str]]],
    prepare_control_messages: Callable[[str, list[str]], list[HumanMessage]],
    resolve_project_instructions: Callable[[str], str | None] | None = None,
    resolve_subagent_roles: Callable | None = None,
    resolve_prompt_parts: Callable | None = None,
    observe_turns: bool = False,
    permission_settings: PermissionSettings | None = None,
    resolve_permission_context: Callable = fixed_permission_context,
    permission_reviewer: AutoPermissionReviewer | None = None,
):
    """Resume the same run for steering or a natural-end follow-up."""
    @before_agent
    def reset_run_control(_state, _runtime):
        return {'run_stopped': False}

    @before_model(can_jump_to=['end'])
    def inject_before_model(state, runtime):
        session_id = runtime.context['session_id']
        if is_stop_requested(session_id):
            return {'run_stopped': True, 'jump_to': 'end'}
        messages = drain_steering(session_id)
        if messages:
            prepared = prepare_control_messages(session_id, messages)
            return {'messages': prepared}
        return None

    @after_agent(can_jump_to=['model'])
    def inject_at_natural_end(_state, runtime):
        session_id = runtime.context['session_id']
        stopped, messages = finish_run_boundary(session_id)
        if stopped:
            return {'run_stopped': True}
        if messages:
            prepared = prepare_control_messages(session_id, messages)
            return {
                'messages': prepared,
                'jump_to': 'model',
            }
        return None

    toolset = ToolSetMiddleware(tool_registry, resolve_active_tool_names, system_prompt,
                                resolve_project_instructions, resolve_subagent_roles, resolve_prompt_parts)
    compaction_middleware = make_context_compaction_middleware(
        resolve_model, model_inputs=toolset.model_inputs,
    )
    return _build_runtime_agent(
        default_model, tool_registry=tool_registry, resolve_active_tool_names=resolve_active_tool_names, system_prompt=system_prompt,
        response_format=response_format, resolve_model=resolve_model,
        record_selection=record_selection, checkpointer=checkpointer,
        steering_middleware=[reset_run_control, compaction_middleware,
                             inject_before_model,
                             inject_at_natural_end,
                             recover_input_overflow],
        toolset=toolset, state_schema=InteractiveRunState,
        observe_turns=observe_turns, active_stop=True, permission_settings=permission_settings, resolve_permission_context=resolve_permission_context,
        permission_reviewer=permission_reviewer,
    )


def _build_runtime_agent(
    default_model, *, tool_registry, resolve_active_tool_names, system_prompt, response_format, resolve_model,
    record_selection, checkpointer, steering_middleware, state_schema=None,
    observe_turns=False, active_stop=False, toolset=None, permission_settings=None, resolve_permission_context=fixed_permission_context,
    permission_reviewer=None, model_middleware=(),
):
    permission_settings = permission_settings if permission_settings is not None else PermissionSettings()
    toolset = toolset or ToolSetMiddleware(tool_registry, resolve_active_tool_names, system_prompt)
    return create_agent(
        model=default_model,
        tools=[],
        system_prompt=system_prompt,
        response_format=response_format,
        context_schema=RunContext,
        state_schema=state_schema,
        middleware=[*([StopModelMiddleware()] if active_stop else []), *([TurnBoundaryMiddleware()] if observe_turns else []),
                    make_model_selection_middleware(resolve_model, record_selection),
                    toolset, ToolBatchMiddleware(tool_registry), _record_tool_call,
                    ToolErrorMiddleware(on_error=tool_error_feedback), ExecutionAuthorizationMiddleware(permission_settings),
                    *steering_middleware, BatchApprovalMiddleware(has_checkpointer=checkpointer is not None),
                    PermissionMiddleware(tool_registry, permission_settings, resolve_permission_context, permission_reviewer),
                    ModelInputMiddleware(), observe_model_request, *model_middleware,
                    *([StopBoundaryMiddleware()] if active_stop else [])],
        checkpointer=checkpointer,
    )


def build_configured_runtime_agent(
    config_path,
    *,
    tool_registry: ToolRegistry,
    resolve_active_tool_names: Callable,
    system_prompt: str,
    response_format: Any,
    select_model_id: Callable[[str], str],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None = None,
    permission_settings: PermissionSettings | None = None,
    resolve_permission_context: Callable = fixed_permission_context,
):
    """Load one config snapshot and connect its models to session-aware routing."""
    catalog = load_model_catalog(config_path)
    settings = permission_settings if permission_settings is not None else PermissionSettings()
    reviewer = AutoPermissionReviewer.from_catalog(catalog, settings.auto)

    def resolve_model(session_id: str, agent_path='root') -> tuple[str, BaseChatModel]:
        model_id = select_model_id(session_id)
        return model_id, catalog.get(model_id)

    return build_runtime_agent(
        catalog.get(catalog.default_model_id),
        tool_registry=tool_registry,
        resolve_active_tool_names=resolve_active_tool_names,
        system_prompt=system_prompt,
        response_format=response_format,
        resolve_model=resolve_model,
        record_selection=record_selection,
        checkpointer=checkpointer, permission_settings=permission_settings, resolve_permission_context=resolve_permission_context,
        permission_reviewer=reviewer,
    )
