"""Trusted session state and application run lifecycle."""

from naiyue_test_agent.agent_runtime.observation import observe

import asyncio
import logging
from functools import partial
from collections import deque
from threading import Lock
from uuid import uuid4
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.contracts.failures import safe_error_type
from .session_approval import (SessionApprovalControls, pending_tool_approval, prepare_resume,
                               public_approvals, validate_decisions, validate_remembered, is_pre_tool_approval, root_approval_batch)
from naiyue_test_agent.tools.permission_rules import PermissionSettings
from naiyue_test_agent.agent_runtime.tool_execution.permissions import AutoPermissionReviewer
from naiyue_test_agent.tools.read import resolve_workspace_root
from naiyue_test_agent.tools.processes import ProcessCleanupError
from naiyue_test_agent.application.skills import prepare_run_resources
from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions, RunObservation
from naiyue_test_agent.application.session_controls import SessionControlMixin
from naiyue_test_agent.application.session_subagents import SessionSubagentControls
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.registry import ToolRegistry
from naiyue_test_agent.contracts.failures import normalize_classifiers
from naiyue_test_agent.agent_runtime.errors import RuntimeErrorPolicy
from naiyue_test_agent.agent_runtime.manual_compaction import ManualCompactionCommitError
from .failure_policy import ApplicationErrorPolicy
from .errors import SessionBusyError, SessionPendingApprovalError, SessionManualReviewRequiredError
from .io_steps import IOCall, arun_steps
from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run
from naiyue_test_agent.agent_runtime.resource_invocation import (
    ainvoke_agent_run_with_resources, aresume_agent_run_with_resources,
)


logger = logging.getLogger('naiyue_test_agent.application')


class SessionService(SessionControlMixin, SessionSubagentControls, SessionApprovalControls):
    """Map public session IDs to private LangGraph threads and model choices."""

    _busy_error = SessionBusyError

    def __init__(self, catalog: ModelCatalog, tool_registry: ToolRegistry | None = None, *, initial_active_tools=None, permission_settings=None, progress_settings=None):
        self._progress_settings = progress_settings
        self._permission_settings = permission_settings if permission_settings is not None else PermissionSettings()
        self._permission_reviewer = AutoPermissionReviewer.from_catalog(catalog, self._permission_settings.auto)
        self._catalog = catalog
        self._tool_registry = tool_registry if tool_registry is not None else ToolRegistry()
        initial = self._tool_registry.names() if initial_active_tools is None else initial_active_tools
        self._initial_active_tools = tuple(spec.tool.name for spec in self._tool_registry.select(initial))
        self._sessions: dict[str, dict] = {}
        self._subagents: dict[str, dict] = {}
        self._subagent_instances: dict[tuple[str, str], dict] = {}
        self._lock = Lock()
        self._model_selection_lock = Lock()
        self._tool_selection_lock = Lock()
        self._permission_selection_lock = Lock()
        self._events = ApplicationEventSubscriptions()
        self._active_runs = {}
        self._persistence = None
        self._runtime_classifiers = ()
        self._application_classifiers = ()

    def configure_error_classifiers(self, *, model_id=None, model=None, runtime=None, application=None):
        runtime = normalize_classifiers(runtime) if runtime is not None else None
        application = normalize_classifiers(application) if application is not None else None
        model = normalize_classifiers(model) if model is not None else None
        if (model is None) != (model_id is None):
            raise ValueError('Model classifiers require a model_id')
        with self._lock:
            if any(session['busy'] for session in self._sessions.values()):
                raise SessionBusyError('Cannot change error mappings during an active run')
            if model is not None:
                self._catalog.configure_error_classifiers(model_id, model)
            if runtime is not None:
                self._runtime_classifiers = runtime
            if application is not None:
                self._application_classifiers = application

    def subscribe_events(self, handler):
        return self._events.subscribe(handler)

    def create_session(self, model_id: str) -> str:
        return self._create_session(model_id, None)

    def create_workspace_session(self, model_id: str, workspace_root: str | Path) -> str:
        root = resolve_workspace_root(workspace_root)
        return self._create_session(model_id, root)

    def _create_session(self, model_id: str, workspace_root: Path | None) -> str:
        self._catalog.get(model_id)
        with self._lock:
            session_id = uuid4().hex
            while session_id in self._sessions:
                session_id = uuid4().hex
            session = {
                'thread_id': uuid4().hex,
                'model_id': model_id,
                'thinking_level': 'model_default',
                'workspace_root': workspace_root,
                'active_tool_names': self._initial_active_tools,
                'permission_mode': self._permission_settings.mode, 'session_allow': [],
                'pending_tool_approval': None,
                'pending_turn': None,
                'manual_review_required': False,
                'busy': False,
                'run_id': None,
                'run_review_project_instructions': None, 'run_prompt_parts': [],
                'steering_open': False,
                'stop_requested': False,
                'steering_queue': deque(),
                'follow_up_queue': deque(),
            }
            self._sessions[session_id] = session
        try:
            if self._persistence is not None:
                self._persistence.storage.create(session_id, session)
        except BaseException:
            with self._lock:
                self._sessions.pop(session_id, None)
            raise
        return session_id

    def has_session(self, session_id):
        with self._lock:
            return session_id in self._sessions

    def describe_session(self, session_id):
        with self._lock:
            row = self._sessions[session_id]
            return {'session_id': session_id, 'model_id': row['model_id'],
                'thinking_level': row['thinking_level'],
                'active_tools': list(row['active_tool_names']), 'permission_mode': row['permission_mode'],
                'workspace_root': str(row['workspace_root']) if row['workspace_root'] else None,
                'busy': row['busy'], 'run_id': row['run_id'],
                'manual_review_required': row['manual_review_required'],
                'pending_approval': row['pending_tool_approval'] is not None,
                'approval_run_id': (row['pending_tool_approval'].get('requested_run_id')
                    if row['pending_tool_approval'] is not None else None)}

    def resolve_model(self, session_id: str, agent_path='root'):
        with self._lock:
            model_id = (self._sessions[session_id]['model_id'] if agent_path == 'root'
                        else self.subagent_instance(session_id, agent_path)['model_id'])
            level = self._sessions[session_id]['thinking_level'] if agent_path == 'root' else 'model_default'
        return model_id, self._catalog.with_thinking(model_id, level)

    def resolve_prompt_parts(self, session_id):
        with self._lock:
            return list(self._sessions[session_id]['run_prompt_parts'])

    def select_model(self, session_id: str, model_id: str) -> None:
        self._catalog.get(model_id)
        with self._model_selection_lock:
            with self._lock:
                self._sessions[session_id]
            if self._persistence is not None:
                self._persistence.storage.select_model(session_id, model_id)
            with self._lock:
                self._sessions[session_id]['model_id'] = model_id
                self._sessions[session_id]['thinking_level'] = 'model_default'

    def _run_steps(self, agent, session_id, messages, *, run_id=None, max_concurrency=4, compact=False):
        with self._lock:
            session = self._sessions[session_id]
            if session['manual_review_required']:
                raise SessionManualReviewRequiredError(
                    'Session requires manual review after an uncertain tool outcome'
                )
            if session['pending_tool_approval'] is not None:
                raise SessionPendingApprovalError('Session has pending tool approval')
            if session['busy']:
                raise SessionBusyError('Session already has an active run')
            run_id = run_id or uuid4().hex
            observation = RunObservation(
                self._events.publisher(session_id, run_id), 'compact' if compact else 'message', progress_settings=self._progress_settings,
            )
            observation.runtime_errors = RuntimeErrorPolicy(self._runtime_classifiers, self._catalog.failure_describer())
            observation.application_errors = ApplicationErrorPolicy(self._application_classifiers)
            session['busy'] = True
            session['steering_open'] = not compact
            session['stop_requested'] = False
            session['steering_queue'].clear()
            session['follow_up_queue'].clear()
            thread_id = session['thread_id']
            workspace_root = session['workspace_root']
            session['run_id'] = run_id
            self._active_runs[session_id] = observation

        primary_error = None
        review_required = False
        try:
            observation.start()
            if self._persistence is not None:
                yield IOCall(self._persistence.start, (session_id, run_id))
            resources = yield IOCall(prepare_run_resources, (workspace_root,),
                {'enable_skills': 'read' in self._tool_registry.names()})
            with self._lock:
                session['run_prompt_parts'] = resources['prompt_parts']
                session['run_review_project_instructions'] = resources['project_instructions']
            messages = self._mark_human_messages(messages)
            observation.stage = 'execute'
            if compact:
                from .session_compaction import compact_session
                with observation.bind():
                    result = yield IOCall(args=(self, agent, session_id, thread_id, run_id),
                        async_call=compact_session, critical=False)
            elif workspace_root is not None:
                with observation.bind(ainvoke_subagent=self.ainvoke_subagent):
                    result = yield IOCall(args=(agent, messages),
                        kwargs=dict(session_id=session_id, thread_id=thread_id, run_id=run_id,
                             workspace_root=workspace_root, read_resource_roots=resources['read_resource_roots']),
                        async_call=partial(ainvoke_agent_run_with_resources, max_concurrency=max_concurrency),
                        critical=False)
            else:
                with observation.bind(ainvoke_subagent=self.ainvoke_subagent):
                    result = yield IOCall(args=(agent, messages),
                        kwargs=dict(session_id=session_id, thread_id=thread_id, run_id=run_id),
                        async_call=partial(ainvoke_agent_run, max_concurrency=max_concurrency), critical=False)
            observation.stage = 'finalize'
            pending = yield IOCall(pending_tool_approval, (result,))
            if pending is not None:
                with self._lock:
                    pending['requested_run_id'] = run_id
                    session['pending_tool_approval'] = pending
                self.announce_approval(observation, root_approval_batch(session_id, pending))
                result['approval_requests'] = public_approvals(pending)
                self._record_approval('approval_requested', session_id, run_id,
                                      count=len(pending['items']))
            observation.result(result, pending)
            return result
        except BaseException as error:
            primary_error = error
            if isinstance(error, asyncio.CancelledError):
                observation.cancelled(error)
            else:
                observation.failed(error)
            partial_tools = False
            if isinstance(error, Exception) and not isinstance(error, GraphBubbleUp):
                partial_tools = yield IOCall(args=(agent, session_id),
                                            async_call=self._areview_partial_state)
            if partial_tools or isinstance(error, (ProcessCleanupError, ManualCompactionCommitError)) or (
                    isinstance(error, asyncio.CancelledError)):
                with self._lock:
                    session['manual_review_required'] = True
                    session['pending_tool_approval'] = None
                logger.error('session_manual_review_required', extra={'session_event': {
                    'event': 'session_manual_review_required',
                    'session_id': session_id, 'run_id': run_id,
                }})
            raise
        finally:
            yield IOCall(args=(session_id, run_id, session, agent, observation, primary_error),
                         async_call=self._afinish_run)

    @staticmethod
    def _record_approval(event: str, session_id: str, run_id: str, **counts) -> None:
        logger.info(event, extra={'approval_event': {
            'event': event, 'session_id': session_id, 'run_id': run_id, **counts,
        }})

    def pending_tool_approvals(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            pending = self._sessions[session_id]['pending_tool_approval']
            return public_approvals(pending) if pending is not None else []

    def _resume_steps(self, agent, session_id, decisions, *, run_id=None, max_concurrency=4, remember_approved=None, stop=False):
        with self._lock:
            session = self._sessions[session_id]
            if session['manual_review_required']:
                raise SessionManualReviewRequiredError(
                    'Session requires manual review after an uncertain tool outcome'
                )
            pending = session['pending_tool_approval']
            if pending is None:
                raise SessionPendingApprovalError('Session has no pending tool approval')
            if session['busy']:
                raise SessionBusyError('Session already has an active run')
            if not stop:
                validate_decisions(pending, decisions)
                validate_remembered(pending, decisions, remember_approved)
            run_id = run_id or uuid4().hex
            observation = RunObservation(
                self._events.publisher(session_id, run_id), 'approval_resume', progress_settings=self._progress_settings,
            )
            observation.runtime_errors = RuntimeErrorPolicy(self._runtime_classifiers, self._catalog.failure_describer())
            observation.application_errors = ApplicationErrorPolicy(self._application_classifiers)
            observation.turns.restore(session['pending_turn'])
            if stop:
                observation.stop_control.request()
                observe(observation.controls, observation.controls.accept, 'stop')
            else:
                observe(observation.controls, observation.controls.accept, 'approval',
                    tool_call_ids=tuple(item['tool_call_id'] for item in pending['items']),
                    decisions=tuple(decisions), origin_run_id=pending.get('requested_run_id'))
            session['busy'] = True
            session['steering_open'] = not stop
            session['stop_requested'] = stop
            thread_id = session['thread_id']
            workspace_root = session['workspace_root']
            session['run_id'] = run_id
            self._active_runs[session_id] = observation

        primary_error = None
        review_required = False
        try:
            observation.start()
            if self._persistence is not None:
                yield IOCall(self._persistence.start, (session_id, run_id))
            grants, remembered = (None, []) if stop else prepare_resume(session_id, pending, decisions, remember_approved)
            with self._lock:
                session['session_allow'].extend(rule for rule in remembered if rule not in session['session_allow'])
            resources = yield IOCall(prepare_run_resources, (workspace_root,),
                {'enable_skills': 'read' in self._tool_registry.names()})
            with self._lock:
                session['run_prompt_parts'] = resources['prompt_parts']
                session['run_review_project_instructions'] = resources['project_instructions']
            self._record_approval(
                'approval_stop_submitted' if stop else 'approval_review_submitted', session_id, run_id,
                approved_count=decisions.count('approve'),
                rejected_count=decisions.count('reject'),
            )
            observation.stage = 'execute'
            with observation.bind(ainvoke_subagent=self.ainvoke_subagent):
                result = yield IOCall(args=(agent,),
                    kwargs=dict(session_id=session_id, thread_id=thread_id, run_id=run_id, workspace_root=workspace_root,
                         decisions=decisions, approval_grants=grants, read_resource_roots=resources['read_resource_roots']),
                    async_call=partial(aresume_agent_run_with_resources, max_concurrency=max_concurrency),
                    critical=False)
            observation.stage = 'finalize'
            next_pending = yield IOCall(pending_tool_approval, (result,))
            with self._lock:
                if next_pending is not None:
                    next_pending['requested_run_id'] = run_id
                session['pending_tool_approval'] = next_pending
            result['resume_start_index'] = pending['message_count']
            self._record_approval('approval_resumed', session_id, run_id,
                                  count=len(pending['items']))
            if next_pending is not None:
                result['approval_requests'] = public_approvals(next_pending)
                self.announce_approval(observation, root_approval_batch(session_id, next_pending))
                self._record_approval('approval_requested', session_id, run_id,
                                      count=len(next_pending['items']))
            observation.result(result, next_pending)
            return result
        except BaseException as error:
            primary_error = error
            if isinstance(error, asyncio.CancelledError):
                observation.cancelled(error)
            else:
                observation.failed(error)
            partial_tools = False
            if isinstance(error, Exception) and not isinstance(error, GraphBubbleUp):
                partial_tools = yield IOCall(args=(agent, session_id),
                                            async_call=self._areview_partial_state)
            if partial_tools or isinstance(error, ProcessCleanupError) or (
                    isinstance(error, asyncio.CancelledError)):
                with self._lock:
                    session['manual_review_required'] = True
                    session['pending_tool_approval'] = None
                logger.error('session_manual_review_required', extra={'session_event': {
                    'event': 'session_manual_review_required',
                    'session_id': session_id, 'run_id': run_id,
                }})
            raise
        finally:
            yield IOCall(args=(session_id, run_id, session, agent, observation, primary_error),
                         async_call=self._afinish_run)


    def _cleanup_run(self, session_id, run_id, session, observation):
        with self._lock:
            review_required = session['manual_review_required']
            session['pending_turn'] = observe(observation.turns, observation.turns.snapshot,
                pending=session['pending_tool_approval'] is not None)
            discarded = len(session['steering_queue'])
            session['steering_queue'].clear()
            discarded_follow_up = len(session['follow_up_queue'])
            session['follow_up_queue'].clear()
            observe(observation.controls, observation.controls.close, observation.control_close_reason())
            self._active_runs.pop(session_id, None)
            session['steering_open'] = False
            session['stop_requested'] = False
            session['run_prompt_parts'] = []
            session['run_review_project_instructions'] = None
            if self._persistence is None:
                session['busy'] = False
                session['run_id'] = None
        if discarded:
            self._record_steering('steering_discarded', session_id, run_id, discarded)
        if discarded_follow_up:
            self._record_follow_up('follow_up_discarded', session_id, run_id, discarded_follow_up)
        return review_required

    async def _areview_partial_state(self, agent, session_id):
        return await arun_steps(self._partial_review_steps(agent, session_id))

    def _partial_review_steps(self, agent, session_id):
        with self._lock:
            thread = self._sessions[session_id]['thread_id']
        try:
            snapshot = yield IOCall(args=({'configurable': {'thread_id': thread}},),
                                    async_call=agent.aget_state)
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

    async def arun(self, agent, session_id, messages, *, run_id=None, max_concurrency=4):
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError('Tool concurrency must be a positive integer')
        return await arun_steps(self._run_steps(agent, session_id, messages,
            run_id=run_id, max_concurrency=max_concurrency))

    async def acompact(self, agent, session_id, *, run_id=None):
        return await arun_steps(self._run_steps(agent, session_id, [], run_id=run_id, compact=True))

    async def aresume_tool_approval(self, agent, session_id, decisions, *, run_id=None, max_concurrency=4, remember_approved=None, stop=False):
        if type(max_concurrency) is not int or max_concurrency <= 0:
            raise ValueError('Tool concurrency must be a positive integer')
        return await arun_steps(self._resume_steps(agent, session_id, decisions,
            run_id=run_id, max_concurrency=max_concurrency, remember_approved=remember_approved, stop=stop))

    async def _afinish_run(self, session_id, run_id, session, agent, observation, primary_error):
        secondary = None
        review_required = session['manual_review_required']
        try:
            if primary_error is not None:
                self._request_scope_stop(session_id, run_id=run_id, reason='failure')
            await self.settle_subagent_drivers(session_id, run_id)
            self.assert_subagents_settled(session_id, run_id)
            review_required = self._cleanup_run(session_id, run_id, session, observation)
        except Exception as error:
            if primary_error is None:
                secondary = error
                observation.failed(error, stage='cleanup')
            else:
                logger.error('session_cleanup_failed error_type=%s', safe_error_type(error))
        try:
            if self._persistence is not None:
                await self._persistence.afinish(session_id, run_id, agent, observation)
        except Exception as error:
            if primary_error is None and secondary is None:
                secondary = error
                observation.failed(error, stage='finalize')
            else:
                logger.error('session_persistence_secondary_failed error_type=%s', safe_error_type(error))
        finally:
            if self._persistence is not None:
                with self._lock:
                    session['busy'] = False
                    session['run_id'] = None
                    session['steering_open'] = False
                    session['stop_requested'] = False
                    session['run_prompt_parts'] = []
                    session['run_review_project_instructions'] = None
                    session['steering_queue'].clear()
                    session['follow_up_queue'].clear()
                    self._active_runs.pop(session_id, None)
        try:
            observation.finish(review_required)
        except Exception as error:
            if primary_error is None and secondary is None and observation.failure is None:
                raise
            logger.error('run_event_publish_failed error_type=%s', safe_error_type(error))
        if secondary is not None:
            raise secondary
