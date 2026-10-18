"""Assembly and model routing for the LangChain agent loop."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ToolErrorMiddleware, after_agent, before_agent, before_model,
)
from langchain_core.language_models import BaseChatModel
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
from naiyue_test_agent.model_access.messages import ModelInputMiddleware
from .errors import RuntimeErrorPolicy
from naiyue_test_agent.tools.registry import ToolRegistry


from naiyue_test_agent.agent_runtime.call_observation import (
    make_model_selection_middleware,
)
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, AgentEventPublisher
from .reply_identity import ReplyIdentityMiddleware
from .active_stop import StopModelMiddleware, StopBoundaryMiddleware
from .run_control import RunControl
from .session_port import SessionPort
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
    checkpointer: BaseCheckpointSaver | None = None,
    permission_settings: PermissionSettings | None = None,
    resolve_permission_context: Callable = fixed_permission_context,
    permission_reviewer: AutoPermissionReviewer | None = None,
):
    """Own LangChain agent assembly while callers supply task-specific policy."""
    # Standalone graphs use the common tool/permission pipeline; interactive
    # stop, turn observation and root steering are enabled by the other builders.
    return _build_runtime_agent(
        default_model, tool_registry=tool_registry, resolve_active_tool_names=resolve_active_tool_names, system_prompt=system_prompt,
        response_format=response_format, resolve_model=resolve_model,
        checkpointer=checkpointer,
        steering_middleware=[], permission_settings=permission_settings, resolve_permission_context=resolve_permission_context,
        permission_reviewer=permission_reviewer,
    )


def _root_session_id(runtime):
    context = runtime.context or {}
    if context.get('agent_path', 'root') != 'root':
        raise ValueError('Interactive runtime requires root agent_path')
    return context['session_id']


def _assemble_interactive_runtime(
    default_model: BaseChatModel,
    *,
    tool_registry: ToolRegistry,
    session_port: SessionPort,
    system_prompt: str,
    response_format: Any,
    checkpointer: BaseCheckpointSaver | None,
    observe_turns: bool = False,
    permission_settings: PermissionSettings | None = None,
    permission_reviewer: AutoPermissionReviewer | None = None,
):
    """Resume the same run for steering or a natural-end follow-up."""
    @before_agent
    def reset_run_control(_state, runtime):
        _root_session_id(runtime)
        return {'run_stopped': False}

    @before_model(can_jump_to=['end'])
    def inject_before_model(state, runtime):
        session_id = _root_session_id(runtime)
        if session_port.is_stop_requested(session_id):
            return {'run_stopped': True, 'jump_to': 'end'}
        messages = session_port.drain_steering(session_id)
        if messages:
            prepared = session_port.prepare_control_messages(session_id, messages)
            return {'messages': prepared, 'active_reply_id': prepared[-1].additional_kwargs.get('naiyue_reply_id')}
        return None

    @after_agent(can_jump_to=['model'])
    def inject_at_natural_end(_state, runtime):
        session_id = _root_session_id(runtime)
        stopped, messages = session_port.finish_run_boundary(session_id)
        if stopped:
            return {'run_stopped': True}
        if messages:
            prepared = session_port.prepare_control_messages(session_id, messages)
            return {
                'messages': prepared, 'active_reply_id': prepared[-1].additional_kwargs.get('naiyue_reply_id'),
                'jump_to': 'model',
            }
        return None

    toolset = ToolSetMiddleware(tool_registry, session_port.resolve_active_tool_names, system_prompt,
                                resolve_subagent_roles=session_port.get_registered_subagents,
                                resolve_prompt_parts=session_port.resolve_prompt_parts)
    compaction_middleware = make_context_compaction_middleware(
        session_port.resolve_model, model_inputs=toolset.model_inputs,
    )
    # Root only: freeze the tool view before compaction; steering enters before
    # model requests and follow-ups at natural end. Children do not drain these.
    return _build_runtime_agent(
        default_model, tool_registry=tool_registry, resolve_active_tool_names=session_port.resolve_active_tool_names, system_prompt=system_prompt,
        response_format=response_format, resolve_model=session_port.resolve_model,
        checkpointer=checkpointer,
        steering_middleware=[reset_run_control, compaction_middleware,
                             inject_before_model,
                             inject_at_natural_end,
                             recover_input_overflow],
        toolset=toolset, state_schema=InteractiveRunState,
        observe_turns=observe_turns, active_stop=True, permission_settings=permission_settings, resolve_permission_context=session_port.resolve_permission_context,
        permission_reviewer=permission_reviewer,
    )


def _build_runtime_agent(
    default_model, *, tool_registry, resolve_active_tool_names, system_prompt, response_format, resolve_model,
    checkpointer, steering_middleware, state_schema=None,
    observe_turns=False, active_stop=False, toolset=None, permission_settings=None, resolve_permission_context=fixed_permission_context,
    permission_reviewer=None, model_middleware=(),
):
    permission_settings = permission_settings if permission_settings is not None else PermissionSettings()
    toolset = toolset or ToolSetMiddleware(tool_registry, resolve_active_tool_names, system_prompt)
    # Framework order: before hooks forward, after hooks reversed, first wrap outer.
    # Model wraps: select_model -> toolset -> [overflow recovery] -> ModelInput ->
    # request observation -> [child failure capture] -> model (stop wraps outside).
    # after_model: [stop] -> permissions -> batch approval -> [extra checks] ->
    # batch plan -> [turn ready]. Interrupts/errors/jumps may skip later hooks.
    # Tool wraps: toolset -> batch dispatch -> call observation -> ToolError ->
    # execution authorization -> body. Turn completion still requires paired results.
    # Behavior evidence: test_runtime_middleware_contracts, plus framework order
    # in test_agent_middleware_order and the Auto/stop/turn/subagent suites.
    return create_agent(
        model=default_model,
        tools=[],
        system_prompt=system_prompt,
        response_format=response_format,
        context_schema=RunContext,
        state_schema=state_schema,
        middleware=[ReplyIdentityMiddleware(), *([StopModelMiddleware()] if active_stop else []), *([TurnBoundaryMiddleware()] if observe_turns else []),
                    make_model_selection_middleware(resolve_model),
                    toolset, ToolBatchMiddleware(tool_registry), _record_tool_call,
                    ToolErrorMiddleware(on_error=tool_error_feedback), ExecutionAuthorizationMiddleware(permission_settings),
                    *steering_middleware, BatchApprovalMiddleware(has_checkpointer=checkpointer is not None),
                    PermissionMiddleware(tool_registry, permission_settings, resolve_permission_context, permission_reviewer),
                    ModelInputMiddleware(), observe_model_request, *model_middleware,
                    *([StopBoundaryMiddleware()] if active_stop else [])],
        checkpointer=checkpointer,
    )
