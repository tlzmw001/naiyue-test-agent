"""One AgentSession's root execution, failure handling and required finish."""

import asyncio
import logging
from copy import deepcopy
from naiyue_test_agent.contracts.run_context import RunIdentity

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking
from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run
from naiyue_test_agent.agent_runtime.observation import observe
from naiyue_test_agent.agent_runtime.context_usage import compose_model_prompt
from naiyue_test_agent.agent_runtime.manual_compaction import ManualCompactionCommitError, compact_checkpoint
from naiyue_test_agent.agent_runtime.resource_invocation import (
    ainvoke_agent_run_with_resources, aresume_agent_run_with_resources,
)
from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.tools.processes import ProcessCleanupError
from .session_approval import announce_approval, pending_tool_approval, prepare_resume, public_approvals, is_pre_tool_approval, root_approval_batch, validate_decisions, validate_remembered
from .session_controls import mark_human_messages
from .skills import prepare_run_resources


from .session_state import SessionRuntime, ActiveRun
from .session_controls import ControlQueue
from .session_approval import ApprovalBook
from naiyue_test_agent.application.subagents.session_subagents import SubagentRegistry
from naiyue_test_agent.application.errors import (SessionBusyError, SessionManualReviewRequiredError,
    SessionPendingApprovalError, RunCapacityExceededError)


logger = logging.getLogger('naiyue_test_agent.application')


class RunLifecycle:
    """Admit, execute and settle a root run using explicit collaborators."""

    def __init__(self, runtime: SessionRuntime, *, controls: ControlQueue, approvals: ApprovalBook,
                 subagents: SubagentRegistry, tool_registry, persistence, registered_subagents,
                 invoke_subagent, process_tasks, archive_root, mcp_resources, resolve_model, active_tools, memory=None):
        self._runtime, self.session_id = runtime, runtime.session_id
        self._controls, self._approvals, self._subagents = controls, approvals, subagents
        self._tool_registry, self._persistence = tool_registry, persistence
        self._registered_subagents, self._invoke_subagent = registered_subagents, invoke_subagent
        self._process_tasks, self._archive_root, self._mcp_resources = process_tasks, archive_root, mcp_resources
        self._resolve_model, self._active_tools = resolve_model, active_tools
        self._memory = memory
        self._memory_prompt_parts = []

    async def _prepare_resources(self, run, *, include_background=False, messages=()):
        workspace_root = run.execution_root.path if run.execution_root is not None else None
        notices = (self._process_tasks.notice_snapshot(session_id=self.session_id,
            workspace_root=workspace_root, run_id=run.run_id)
            if include_background and self._process_tasks is not None else None)
        resources = await run_blocking(prepare_run_resources, workspace_root,
                                      enable_skills='read' in self._tool_registry.names())
        if include_background and self._persistence is not None:
            _,model=self._resolve_model()
            self._memory_prompt_parts=await self._persistence.prepare_memory(self.session_id,workspace_root,messages,model)
        # One bounded snapshot per logical turn; approval resumes never search again.
        resources['prompt_parts'].extend(deepcopy(self._memory_prompt_parts))
        self.freeze_run_resources(run, resources['prompt_parts'], resources['project_instructions'], notices)
        return resources

    async def _on_failure(self, agent, run, error, *, require_review=False):
        session_id, run_id, observation = self.session_id, run.run_id, run.observation
        if isinstance(error, asyncio.CancelledError):
            observation.cancelled(error)
        else:
            observation.failed(error)
        partial_tools = False
        if isinstance(error, Exception) and not isinstance(error, GraphBubbleUp):
            partial_tools = await await_settled(self._areview_partial_state(agent))
        if partial_tools or require_review or isinstance(error, (ProcessCleanupError, asyncio.CancelledError)):
            self._approvals.mark_manual_review(run)
            logger.error('session_manual_review_required', extra={'session_event': {
                'event': 'session_manual_review_required', 'session_id': session_id, 'run_id': run_id,
            }})

    async def arun(self, agent, messages, *, run,
                    max_concurrency=4, compact=False, system_prompt=None):
        session_id, run_id, observation = self.session_id, run.run_id, run.observation
        facts = self.snapshot_for_run()
        thread_id, workspace_root = facts['thread_id'], facts['workspace_root']
        options = {'durability': 'sync'} if self._persistence is not None else {}

        primary_error = None
        try:
            observation.start()
            if self._persistence is not None:
                await run_blocking(self._persistence.start, session_id, run_id)
            messages = mark_human_messages(messages)
            resources = await self._prepare_resources(run, include_background=not compact and any(
                isinstance(message, HumanMessage) and message.additional_kwargs.get('naiyue_human_input')
                for message in messages),messages=messages)
            if messages:
                run.reply_id = messages[-1].additional_kwargs.get('naiyue_reply_id')
            observation.stage = 'execute'
            if compact:
                model_id, model = self._resolve_model()
                from naiyue_test_agent.tools.subagent import subagent_request_tools
                specs = self._tool_registry.select_available(subagent_request_tools(self._active_tools(), self._tool_registry))
                roles = [(role.name, role.description) for role in self._registered_subagents()] if any(
                    spec.tool.name == 'subagent' for spec in specs) else ()
                system = compose_model_prompt(system_prompt, self.resolve_prompt_parts(), specs, roles)
                with observation.bind(identity=RunIdentity(session_id, run_id, project_id=run.project_id, execution_root=run.execution_root)):
                    result = await compact_checkpoint(agent, session_id=session_id, thread_id=thread_id,
                        run_id=run_id, model_id=model_id, model=model, system_message=system,
                        tools=[spec.tool for spec in specs])
            elif workspace_root is not None:
                with observation.bind(identity=RunIdentity(session_id, run_id, project_id=run.project_id, execution_root=run.execution_root), archive_root=self._archive_root, ainvoke_subagent=self._invoke_subagent, alist_subagents=self._subagents.alist_subagents, process_tasks=self._process_tasks,
                                      background_notices=run.background_notices,
                                      history_query=getattr(self._persistence, 'aquery_history', None) if run.project_id is not None else None,
                                      memory_query=self._memory.query if self._memory is not None and run.project_id is not None else None,
                                      task_queries=getattr(self._persistence.sessions,'task_queries',None) if self._persistence is not None else self._process_tasks):
                    result = await ainvoke_agent_run_with_resources(agent, messages,
                        session_id=session_id, thread_id=thread_id, run_id=run_id, workspace_root=workspace_root,
                        read_resource_roots=resources['read_resource_roots'], max_concurrency=max_concurrency, **options)
            else:
                with observation.bind(identity=RunIdentity(session_id, run_id, project_id=run.project_id, execution_root=run.execution_root), archive_root=self._archive_root, ainvoke_subagent=self._invoke_subagent, alist_subagents=self._subagents.alist_subagents, process_tasks=self._process_tasks,
                                      background_notices=run.background_notices,
                                      history_query=getattr(self._persistence, 'aquery_history', None) if run.project_id is not None else None,
                                      memory_query=self._memory.query if self._memory is not None and run.project_id is not None else None,
                                      task_queries=getattr(self._persistence.sessions,'task_queries',None) if self._persistence is not None else self._process_tasks):
                    result = await ainvoke_agent_run(agent, messages, session_id=session_id,
                        thread_id=thread_id, run_id=run_id, max_concurrency=max_concurrency, **options)
            observation.stage = 'finalize'
            pending = pending_tool_approval(result)
            if pending is not None:
                pending['requested_run_id'] = run_id
                self._approvals.replace_pending_approval(run, pending)
                announce_approval(observation, root_approval_batch(session_id, pending))
                result['approval_requests'] = public_approvals(pending)
                self._record_approval('approval_requested', session_id, run_id,
                                      count=len(pending['items']))
            observation.result(result, pending)
            return result
        except BaseException as error:
            primary_error = error
            await self._on_failure(agent, run, error,
                require_review=isinstance(error, ManualCompactionCommitError))
            raise
        finally:
            await await_settled(self._afinish_run(run, agent, primary_error))

    @staticmethod
    def _record_approval(event: str, session_id: str, run_id: str, **counts) -> None:
        logger.info(event, extra={'approval_event': {
            'event': event, 'session_id': session_id, 'run_id': run_id, **counts,
        }})

    def _cleanup_run(self, run):
        review_required, discarded, discarded_follow_up = self._controls.close_control_window(run)
        if discarded:
            self._controls._record_steering('steering_discarded', self.session_id, run.run_id, discarded)
        if discarded_follow_up:
            self._controls._record_follow_up('follow_up_discarded', self.session_id, run.run_id, discarded_follow_up)
        return review_required

    async def _areview_partial_state(self, agent):
        with self._runtime.lock:
            thread = self._runtime.state.thread_id
        try:
            snapshot = await await_settled(agent.aget_state({'configurable': {'thread_id': thread}}))
        except BaseException as error:
            logger.error('partial_state_review_failed error_type=%s', safe_error_type(error))
            return True
        messages = snapshot.values.get('messages', [])
        if is_pre_tool_approval(snapshot, messages):
            return False
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], AIMessage):
                calls = {call.get('id') for call in messages[index].tool_calls}
                results = {m.tool_call_id for m in messages[index + 1:] if isinstance(m, ToolMessage)}
                return bool(calls - results)
        return False

    async def aresume_tool_approval(self, agent, decisions, *, run,
                                    max_concurrency=4, remember_approved=None, stop=False):
        session_id, run_id, observation = self.session_id, run.run_id, run.observation
        facts = self.snapshot_for_run()
        pending = facts['pending']
        thread_id, workspace_root = facts['thread_id'], facts['workspace_root']
        options = {'durability': 'sync'} if self._persistence is not None else {}

        primary_error = None
        try:
            observation.start()
            if self._persistence is not None:
                await run_blocking(self._persistence.start, session_id, run_id)
            grants, remembered = (None, []) if stop else prepare_resume(session_id, pending, decisions, remember_approved)
            self._approvals.remember_approved_rules(run, remembered)
            # Stopping pairs saved calls; it does not start filesystem or model work.
            resources = {'read_resource_roots': ()} if stop else await self._prepare_resources(run)
            self._record_approval(
                'approval_stop_submitted' if stop else 'approval_review_submitted', session_id, run_id,
                approved_count=decisions.count('approve'),
                rejected_count=decisions.count('reject'),
            )
            observation.stage = 'execute'
            with observation.bind(identity=RunIdentity(session_id, run_id, project_id=run.project_id, execution_root=run.execution_root), archive_root=self._archive_root, ainvoke_subagent=self._invoke_subagent, alist_subagents=self._subagents.alist_subagents, process_tasks=self._process_tasks,
                                  history_query=getattr(self._persistence, 'aquery_history', None) if run.project_id is not None else None,
                                  memory_query=self._memory.query if self._memory is not None and run.project_id is not None else None,
                                      task_queries=getattr(self._persistence.sessions,'task_queries',None) if self._persistence is not None else self._process_tasks):
                result = await aresume_agent_run_with_resources(agent, session_id=session_id,
                    thread_id=thread_id, run_id=run_id, workspace_root=workspace_root, decisions=decisions,
                    approval_grants=grants, read_resource_roots=resources['read_resource_roots'],
                    max_concurrency=max_concurrency, **options)
            observation.stage = 'finalize'
            next_pending = pending_tool_approval(result)
            if next_pending is not None:
                next_pending['requested_run_id'] = run_id
            self._approvals.replace_pending_approval(run, next_pending)
            result['resume_start_index'] = pending['message_count']
            self._record_approval('approval_resumed', session_id, run_id,
                                  count=len(pending['items']))
            if next_pending is not None:
                result['approval_requests'] = public_approvals(next_pending)
                announce_approval(observation, root_approval_batch(session_id, next_pending))
                self._record_approval('approval_requested', session_id, run_id,
                                      count=len(next_pending['items']))
            observation.result(result, next_pending)
            return result
        except BaseException as error:
            primary_error = error
            await self._on_failure(agent, run, error)
            raise
        finally:
            await await_settled(self._afinish_run(run, agent, primary_error))

    async def _afinish_run(self, run, agent, primary_error):
        session_id, run_id, observation = self.session_id, run.run_id, run.observation
        secondary = None
        review_required = self.snapshot_for_finish()['manual_review_required']
        try:
            if primary_error is not None:
                self._controls.request_scope_stop(run_id=run_id, reason='failure')
            await self._subagents.settle_subagent_drivers(run_id)
            self._subagents.assert_subagents_settled(run_id)
            review_required = self._cleanup_run(run)
        except Exception as error:
            if primary_error is None:
                secondary = error
                observation.failed(error, stage='cleanup')
            else:
                logger.error('session_cleanup_failed error_type=%s', safe_error_type(error))
            # A failed cleanup diagnostic must not retain an otherwise settled
            # run. Keep the record if child work is still owned by it.
            if run.controls_open and not run.subagent_instances:
                try:
                    review_required, _, _ = self._controls.close_control_window(run)
                except Exception as close_error:
                    logger.error('session_control_close_failed error_type=%s', safe_error_type(close_error))
        try:
            if self._persistence is not None:
                await self._persistence.afinish(session_id, run_id, agent, observation, self.snapshot_for_finish())
        except Exception as error:
            if primary_error is None and secondary is None:
                secondary = error
                observation.failed(error, stage='finalize')
            else:
                logger.error('session_persistence_secondary_failed error_type=%s', safe_error_type(error))
        finally:
            if not run.controls_open and not run.subagent_instances:
                self.release_run(run)
        try:
            observation.finish(review_required)
        except Exception as error:
            if primary_error is None and secondary is None and observation.failure is None:
                raise
            logger.error('run_event_publish_failed error_type=%s', safe_error_type(error))
        if secondary is not None:
            raise secondary

    def activate_regenerated_thread(self, run, thread_id, revision):
        with self._runtime.lock:
            self._runtime.require_run(run)
            self._runtime.state.thread_id = thread_id
            self._runtime.state.conversation_revision = revision

    def snapshot_for_finish(self):
        with self._runtime.lock:
            return {'thread_id': self._runtime.state.thread_id,
                    'manual_review_required': self._runtime.state.manual_review_required}

    def snapshot_for_run(self):
        with self._runtime.lock:
            row = self._runtime.state
            root = self._runtime.run.execution_root if self._runtime.run else row.execution_root
            return {'thread_id': row.thread_id, 'workspace_root': root.path if root else None,
                'project_id': self._runtime.run.project_id if self._runtime.run else row.project_id, 'execution_root': root,
                'model_id': row.model_id, 'busy': (self._runtime.run is not None), 'run_id': (self._runtime.run.run_id if self._runtime.run else None),
                'stop_requested': (self._runtime.run.stop_requested if self._runtime.run else False), 'steering_open': (self._runtime.run.steering_open if self._runtime.run else False),
                'pending': deepcopy(row.pending_tool_approval),
                'pending_turn': deepcopy(row.pending_turn)}

    def resolve_prompt_parts(self, agent_path='root'):
        with self._runtime.lock:
            parts = self._runtime.run.prompt_parts if self._runtime.run else []
            return deepcopy([part for part in parts if agent_path == 'root' or part.get('id') != 'background_tasks'])

    def admit_run(self, run_id, mode, observation, *, decisions=None, remember_approved=None, stop=False, capacity_available=True, expected_conversation_revision=None):
        resuming = mode == 'approval_resume'
        with self._runtime.lock:
            row = self._runtime.state
            if expected_conversation_revision is not None and row.conversation_revision != expected_conversation_revision:
                raise ValueError('conversation_stale')
            if row.manual_review_required:
                raise SessionManualReviewRequiredError('Session requires manual review after an uncertain tool outcome')
            pending = row.pending_tool_approval
            if resuming:
                if pending is None:
                    raise SessionPendingApprovalError('Session has no pending tool approval')
            elif pending is not None:
                raise SessionPendingApprovalError('Session has pending tool approval')
            if self._runtime.run is not None:
                raise SessionBusyError('Session already has an active run')
            if resuming and not stop:
                validate_decisions(pending, decisions)
                validate_remembered(pending, decisions, remember_approved)
            if not capacity_available:
                raise RunCapacityExceededError('Application root run capacity exceeded')
            if resuming:
                observation.turns.restore(row.pending_turn)
                if stop:
                    observation.stop_control.request()
                    observe(observation.controls, observation.controls.accept, 'stop')
                else:
                    observe(observation.controls, observation.controls.accept, 'approval',
                        tool_call_ids=tuple(item['tool_call_id'] for item in pending['items']),
                        decisions=tuple(decisions), origin_run_id=pending.get('requested_run_id'))
            run = ActiveRun(run_id, observation, project_id=row.project_id, execution_root=row.execution_root, steering_open=mode != 'compact' and not stop,
                            stop_requested=stop)
            self._runtime.run = run
            if self._mcp_resources is not None:
                self._mcp_resources.set_session_occupied(self.session_id, True)
            return run

    def freeze_run_resources(self, run, prompt_parts, project_instructions, background_notices=None):
        with self._runtime.lock:
            self._runtime.require_run(run)
            run.prompt_parts = deepcopy(prompt_parts)
            run.background_notices = background_notices
            if background_notices is not None and background_notices.versions:
                run.prompt_parts.append({'id': 'background_tasks', 'text': background_notices.text,
                    'count': len(background_notices.versions)})
            run.project_instructions = project_instructions

    def release_run(self, run):
        with self._runtime.lock:
            self._runtime.require_run(run)
            if run.controls_open or run.subagent_instances:
                raise SessionBusyError('Run cleanup has not settled')
            self._runtime.run = None
            if self._mcp_resources is not None:
                self._mcp_resources.set_session_occupied(self.session_id, self._runtime.state.pending_tool_approval is not None)
