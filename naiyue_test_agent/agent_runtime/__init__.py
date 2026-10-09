"""Assembly, model routing, and synchronous events for the LangChain agent loop."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentState, HumanInTheLoopMiddleware, ModelRequest, after_agent, after_model,
    before_agent, before_model,
    wrap_model_call, wrap_tool_call,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from naiyue_test_agent.agent_runtime.tool_batch import ToolBatchMiddleware
from naiyue_test_agent.agent_runtime.context_observation import observe_model_request
from naiyue_test_agent.agent_runtime.context_compaction import (
    make_context_compaction_middleware,
)
from naiyue_test_agent.agent_runtime.context_recovery import recover_input_overflow
from naiyue_test_agent.agent_runtime.project_prompt import make_project_prompt_middleware
from naiyue_test_agent.agent_runtime.tool_approval import (
    OneUseApprovalGrants, approval_policies_for_tools,
)
from naiyue_test_agent.model_access import load_model_catalog
from naiyue_test_agent.model_access.errors import (
    ModelResponseRejected, classify_model_failure, unknown_failure_diagnostics,
)
from naiyue_test_agent.model_access.stop_reason import (
    MODEL_STOP_REASON_KEY, normalize_stop_reason, unknown_stop_reason_diagnostics,
)


from naiyue_test_agent.agent_runtime.call_observation import (
    make_model_selection_middleware, _record_tool_call, ObservedHumanInTheLoopMiddleware,
)
from naiyue_test_agent.agent_runtime.events import RunEventPublisher, current_run_event_publisher
from naiyue_test_agent.agent_runtime.message_observation import invoke_with_message_observation
from .invocation_context import make_invocation_context
from naiyue_test_agent.agent_runtime.turn_observation import (
    TurnBoundaryMiddleware, TurnObserver, current_turn_observer,
)
from naiyue_test_agent.agent_runtime.control_observation import ControlObserver, current_control_observer
from naiyue_test_agent.agent_runtime.context_operation_observation import (
    ContextOperationObserver, current_context_operation_observer,
)


logger = logging.getLogger(__name__)


class RunContext(TypedDict):
    session_id: str
    run_id: NotRequired[str]
    workspace_root: NotRequired[Path]
    approval_grants: NotRequired[dict[str, OneUseApprovalGrants]]
    read_resource_roots: NotRequired[tuple[Path, ...]]
    _run_event_publisher: NotRequired[RunEventPublisher]
    _turn_observer: NotRequired[TurnObserver]
    _control_observer: NotRequired[ControlObserver]
    _context_observer: NotRequired[ContextOperationObserver]


class InteractiveRunState(AgentState):
    model_turn_seen: NotRequired[bool]
    run_stopped: NotRequired[bool]


def build_runtime_agent(
    default_model: BaseChatModel,
    *,
    tools: list,
    system_prompt: str,
    response_format: Any,
    resolve_model: Callable[[str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Own LangChain agent assembly while callers supply task-specific policy."""
    return _build_runtime_agent(
        default_model, tools=tools, system_prompt=system_prompt,
        response_format=response_format, resolve_model=resolve_model,
        record_selection=record_selection, checkpointer=checkpointer,
        steering_middleware=[],
    )


def build_steerable_runtime_agent(
    default_model: BaseChatModel,
    *,
    tools: list,
    system_prompt: str,
    response_format: Any,
    resolve_model: Callable[[str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None,
    drain_steering: Callable[[str], list[str]],
    finish_steering: Callable[[str], list[str]],
):
    """Add application-owned steering to the existing synchronous agent."""
    return build_interactive_runtime_agent(
        default_model, tools=tools, system_prompt=system_prompt,
        response_format=response_format, resolve_model=resolve_model,
        record_selection=record_selection, checkpointer=checkpointer,
        drain_steering=drain_steering,
        finish_pending_messages=finish_steering,
    )


def build_interactive_runtime_agent(
    default_model: BaseChatModel,
    *,
    tools: list,
    system_prompt: str,
    response_format: Any,
    resolve_model: Callable[[str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None,
    drain_steering: Callable[[str], list[str]],
    finish_pending_messages: Callable[[str], list[str]],
):
    """Preserve the existing interactive assembly contract."""
    return _assemble_interactive_runtime(
        default_model, tools=tools, system_prompt=system_prompt,
        response_format=response_format, resolve_model=resolve_model,
        record_selection=record_selection, checkpointer=checkpointer,
        drain_steering=drain_steering,
        finish_pending_messages=finish_pending_messages,
    )


def _assemble_interactive_runtime(
    default_model: BaseChatModel,
    *,
    tools: list,
    system_prompt: str,
    response_format: Any,
    resolve_model: Callable[[str], tuple[str, BaseChatModel]],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None,
    drain_steering: Callable[[str], list[str]],
    finish_pending_messages: Callable[[str], list[str]],
    is_stop_requested: Callable[[str], bool] | None = None,
    finish_run_boundary: Callable[[str], tuple[bool, list[str]]] | None = None,
    resolve_project_instructions: Callable[[str], str | None] | None = None,
    observe_turns: bool = False,
    prepare_control_messages: Callable[[str, list[str]], list[HumanMessage]] | None = None,
):
    """Resume the same run for steering or a natural-end follow-up."""
    if (is_stop_requested is None) != (finish_run_boundary is None):
        raise ValueError('Both stop callbacks are required together')

    if is_stop_requested is not None:
        @before_agent
        def reset_run_control(_state, _runtime):
            return {'model_turn_seen': False, 'run_stopped': False}

        @after_model
        def mark_model_turn(_state, _runtime):
            return {'model_turn_seen': True}

    @before_model(can_jump_to=['end'])
    def inject_before_model(state, runtime):
        session_id = runtime.context['session_id']
        if is_stop_requested is not None and is_stop_requested(session_id):
            return {'jump_to': 'end'} if state.get('model_turn_seen') else None
        messages = drain_steering(session_id)
        if messages:
            prepared = (prepare_control_messages(session_id, messages) if prepare_control_messages is not None
                        else [HumanMessage(content=text) for text in messages])
            return {'messages': prepared}
        return None

    @after_agent(can_jump_to=['model'])
    def inject_at_natural_end(_state, runtime):
        session_id = runtime.context['session_id']
        if finish_run_boundary is not None:
            stopped, messages = finish_run_boundary(session_id)
            if stopped:
                return {'run_stopped': True}
        else:
            messages = finish_pending_messages(session_id)
        if messages:
            prepared = (prepare_control_messages(session_id, messages) if prepare_control_messages is not None
                        else [HumanMessage(content=text) for text in messages])
            return {
                'messages': prepared,
                'jump_to': 'model',
            }
        return None

    control_middleware = ([reset_run_control, mark_model_turn]
                          if is_stop_requested is not None else [])
    project_prompt_middleware = (
        [make_project_prompt_middleware(resolve_project_instructions)]
        if resolve_project_instructions is not None else []
    )
    compaction_middleware = make_context_compaction_middleware(
        resolve_model, tools=tools, system_prompt=system_prompt,
        resolve_project_instructions=resolve_project_instructions,
    )
    return _build_runtime_agent(
        default_model, tools=tools, system_prompt=system_prompt,
        response_format=response_format, resolve_model=resolve_model,
        record_selection=record_selection, checkpointer=checkpointer,
        steering_middleware=[*control_middleware, compaction_middleware,
                             inject_before_model,
                             inject_at_natural_end, *project_prompt_middleware,
                             recover_input_overflow],
        state_schema=InteractiveRunState if is_stop_requested is not None else None,
        observe_turns=observe_turns,
    )


def _build_runtime_agent(
    default_model, *, tools, system_prompt, response_format, resolve_model,
    record_selection, checkpointer, steering_middleware, state_schema=None,
    observe_turns=False,
):
    approval_rules = {
        name: {'allowed_decisions': ['approve', 'reject'],
               'when': policy.when, 'description': policy.description}
        for name, policy in approval_policies_for_tools(tools).items()
    }
    tool_hitl = []
    if approval_rules:
        if checkpointer is None:
            raise ValueError('Session tool approval requires a checkpointer')
        tool_hitl = [ObservedHumanInTheLoopMiddleware(interrupt_on=approval_rules)]
    return create_agent(
        model=default_model,
        tools=tools,
        system_prompt=system_prompt,
        response_format=response_format,
        context_schema=RunContext,
        state_schema=state_schema,
        middleware=[*([TurnBoundaryMiddleware()] if observe_turns else []),
                    make_model_selection_middleware(resolve_model, record_selection),
                    ToolBatchMiddleware(tools), _record_tool_call,
                    *steering_middleware, *tool_hitl, observe_model_request],
        checkpointer=checkpointer,
    )


def build_configured_runtime_agent(
    config_path,
    *,
    tools: list,
    system_prompt: str,
    response_format: Any,
    select_model_id: Callable[[str], str],
    record_selection: Callable[[str, str], None],
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Load one config snapshot and connect its models to session-aware routing."""
    catalog = load_model_catalog(config_path)

    def resolve_model(session_id: str) -> tuple[str, BaseChatModel]:
        model_id = select_model_id(session_id)
        return model_id, catalog.get(model_id)

    return build_runtime_agent(
        catalog.get(catalog.default_model_id),
        tools=tools,
        system_prompt=system_prompt,
        response_format=response_format,
        resolve_model=resolve_model,
        record_selection=record_selection,
        checkpointer=checkpointer,
    )


def invoke_agent_run(agent, messages, *, session_id: str, thread_id: str, run_id: str):
    """Invoke one synchronous run and record its correlated terminal outcome."""
    return _invoke_agent_run(
        agent, messages, session_id=session_id, thread_id=thread_id,
        run_id=run_id, workspace_root=None,
    )


def invoke_agent_run_in_workspace(
    agent, messages, *, session_id: str, thread_id: str, run_id: str,
    workspace_root: Path,
):
    """Invoke with an application-owned workspace in the private run context."""
    if not isinstance(workspace_root, Path) or not workspace_root.is_absolute():
        raise ValueError('A canonical workspace_root is required')
    return _invoke_agent_run(
        agent, messages, session_id=session_id, thread_id=thread_id,
        run_id=run_id, workspace_root=workspace_root,
    )


def resume_agent_run_in_workspace(
    agent, *, session_id: str, thread_id: str, run_id: str,
    workspace_root: Path, decisions: list[str], approved_read_only_targets: dict[str, Path],
    approved_bash_commands: dict[str, tuple[str, Path]] | None = None,
):
    """Resume one interrupted run with only the reviewed read-only tool grants."""
    if not isinstance(workspace_root, Path) or not workspace_root.is_absolute():
        raise ValueError('A canonical workspace_root is required')
    read_only = OneUseApprovalGrants(approved_read_only_targets)
    bash = OneUseApprovalGrants(approved_bash_commands or {})
    return resume_agent_run_with_grants(
        agent, session_id=session_id, thread_id=thread_id, run_id=run_id,
        workspace_root=workspace_root, decisions=decisions,
        approval_grants={'read': read_only, 'ls': read_only, 'find': read_only,
                         'grep': read_only, 'bash': bash},
    )


def resume_agent_run_with_grants(
    agent, *, session_id: str, thread_id: str, run_id: str,
    workspace_root: Path, decisions: list[str],
    approval_grants: dict[str, OneUseApprovalGrants],
):
    """Resume with application-provided grants, independent of concrete tool types."""
    if not isinstance(workspace_root, Path) or not workspace_root.is_absolute():
        raise ValueError('A canonical workspace_root is required')
    return _invoke_agent_run(
        agent, Command(resume={'decisions': [{'type': item} for item in decisions]}),
        session_id=session_id, thread_id=thread_id, run_id=run_id,
        workspace_root=workspace_root, approval_grants=approval_grants,
    )


def _invoke_agent_run(
    agent, messages, *, session_id: str, thread_id: str, run_id: str,
    workspace_root: Path | None,
    approval_grants: dict[str, OneUseApprovalGrants] | None = None,
    read_resource_roots: tuple[Path, ...] = (),
):
    for name, value in (
        ('session_id', session_id), ('thread_id', thread_id), ('run_id', run_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'A non-empty {name} is required')

    correlation = {'run_id': run_id, 'session_id': session_id}
    logger.info('run_started', extra={'run_event': {'event': 'run_started', **correlation}})
    try:
        context = make_invocation_context(session_id, run_id, workspace_root, approval_grants, read_resource_roots)
        publisher = context.get('_run_event_publisher')
        input = messages if isinstance(messages, Command) else {'messages': messages}
        config = {'configurable': {'thread_id': thread_id}}
        if '_run_event_publisher' in context:
            result = invoke_with_message_observation(
                agent, input, context=context, config=config, publisher=publisher,
            )
        else:
            result = agent.invoke(input, context=context, config=config)
    except Exception:
        logger.error('run_failed', extra={'run_event': {'event': 'run_failed', **correlation}})
        raise

    terminal = ('run_interrupted' if isinstance(result, dict) and result.get('__interrupt__')
                else 'run_stopped' if isinstance(result, dict) and result.get('run_stopped') is True
                else 'run_completed')
    logger.info(terminal, extra={'run_event': {'event': terminal, **correlation}})
    return result
