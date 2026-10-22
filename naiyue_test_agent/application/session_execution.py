"""One AgentSession's root execution, failure handling and required finish."""

import asyncio
import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.agent_runtime.async_utils import await_settled, run_blocking
from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run
from naiyue_test_agent.agent_runtime.context_usage import compose_model_prompt
from naiyue_test_agent.agent_runtime.manual_compaction import ManualCompactionCommitError, compact_checkpoint
from naiyue_test_agent.agent_runtime.resource_invocation import (
    ainvoke_agent_run_with_resources, aresume_agent_run_with_resources,
)
from naiyue_test_agent.contracts.failures import safe_error_type
from naiyue_test_agent.tools.processes import ProcessCleanupError
from .session_approval import (announce_approval, pending_tool_approval, prepare_resume,
                               public_approvals, is_pre_tool_approval, root_approval_batch)
from .session_controls import mark_human_messages
from .skills import prepare_run_resources


logger = logging.getLogger('naiyue_test_agent.application')


class SessionExecution:
    """Internal part of AgentSession; it borrows shared graphs and IO."""

    async def _prepare_resources(self, run, *, include_background=False):
        workspace_root = self.snapshot_for_context()['workspace_root']
        notices = (self._process_tasks.notice_snapshot(session_id=self.session_id,
            workspace_root=workspace_root, run_id=run.run_id)
            if include_background and self._process_tasks is not None else None)
        resources = await run_blocking(prepare_run_resources, workspace_root,
                                      enable_skills='read' in self._tool_registry.names())
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
            self.mark_manual_review(run)
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
                for message in messages))
            if messages:
                run.reply_id = messages[-1].additional_kwargs.get('naiyue_reply_id')
            observation.stage = 'execute'
            if compact:
                model_id, model = self.resolve_model()
                from naiyue_test_agent.tools.subagent import subagent_request_tools
                specs = self._tool_registry.select_available(subagent_request_tools(self.get_active_tools(), self._tool_registry))
                roles = [(role.name, role.description) for role in self._registered_subagents()] if any(
                    spec.tool.name == 'subagent' for spec in specs) else ()
                system = compose_model_prompt(system_prompt, self.resolve_prompt_parts(), specs, roles)
                with observation.bind():
                    result = await compact_checkpoint(agent, session_id=session_id, thread_id=thread_id,
                        run_id=run_id, model_id=model_id, model=model, system_message=system,
                        tools=[spec.tool for spec in specs])
            elif workspace_root is not None:
                with observation.bind(ainvoke_subagent=self._invoke_subagent, alist_subagents=self.alist_subagents, process_tasks=self._process_tasks,
                                      background_notices=run.background_notices):
                    result = await ainvoke_agent_run_with_resources(agent, messages,
                        session_id=session_id, thread_id=thread_id, run_id=run_id, workspace_root=workspace_root,
                        read_resource_roots=resources['read_resource_roots'], max_concurrency=max_concurrency, **options)
            else:
                with observation.bind(ainvoke_subagent=self._invoke_subagent, alist_subagents=self.alist_subagents, process_tasks=self._process_tasks,
                                      background_notices=run.background_notices):
                    result = await ainvoke_agent_run(agent, messages, session_id=session_id,
                        thread_id=thread_id, run_id=run_id, max_concurrency=max_concurrency, **options)
            observation.stage = 'finalize'
            pending = pending_tool_approval(result)
            if pending is not None:
                pending['requested_run_id'] = run_id
                self.replace_pending_approval(run, pending)
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
        review_required, discarded, discarded_follow_up = self.close_control_window(run)
        if discarded:
            self._record_steering('steering_discarded', self.session_id, run.run_id, discarded)
        if discarded_follow_up:
            self._record_follow_up('follow_up_discarded', self.session_id, run.run_id, discarded_follow_up)
        return review_required

    async def _areview_partial_state(self, agent):
        thread = self.get_thread_id()
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
            self.remember_approved_rules(run, remembered)
            resources = await self._prepare_resources(run)
            self._record_approval(
                'approval_stop_submitted' if stop else 'approval_review_submitted', session_id, run_id,
                approved_count=decisions.count('approve'),
                rejected_count=decisions.count('reject'),
            )
            observation.stage = 'execute'
            with observation.bind(ainvoke_subagent=self._invoke_subagent, alist_subagents=self.alist_subagents, process_tasks=self._process_tasks):
                result = await aresume_agent_run_with_resources(agent, session_id=session_id,
                    thread_id=thread_id, run_id=run_id, workspace_root=workspace_root, decisions=decisions,
                    approval_grants=grants, read_resource_roots=resources['read_resource_roots'],
                    max_concurrency=max_concurrency, **options)
            observation.stage = 'finalize'
            next_pending = pending_tool_approval(result)
            if next_pending is not None:
                next_pending['requested_run_id'] = run_id
            self.replace_pending_approval(run, next_pending)
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
                self.request_scope_stop(run_id=run_id, reason='failure')
            await self.settle_subagent_drivers(run_id)
            self.assert_subagents_settled(run_id)
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
                    review_required, _, _ = self.close_control_window(run)
                except Exception as close_error:
                    logger.error('session_control_close_failed error_type=%s', safe_error_type(close_error))
        try:
            if self._persistence is not None:
                await self._persistence.afinish(session_id, run_id, agent, observation, self.snapshot_for_finish())
            elif secondary is None:
                from .subagent_records import finish_memory
                finish_memory(self, run)
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
