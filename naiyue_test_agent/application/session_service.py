"""Trusted session state and application run lifecycle."""

import asyncio
import logging
from functools import partial
from collections import deque
from threading import Lock
from uuid import uuid4
from pathlib import Path

from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from naiyue_test_agent.agent_runtime import invoke_agent_run
from naiyue_test_agent.agent_runtime.events import safe_error_type, bind_run_event_publisher
from naiyue_test_agent.agent_runtime.tool_approval import ApprovalReview, ContextualApprovalPolicy
from naiyue_test_agent.agent_runtime.tools.read import resolve_workspace_root
from naiyue_test_agent.agent_runtime.tools.write import WriteOutcomeUnknown
from naiyue_test_agent.agent_runtime.tools.bash import BashOutcomeUnknown
from naiyue_test_agent.agent_runtime.resource_invocation import (
    invoke_agent_run_with_resources, resume_agent_run_with_resources,
)
from naiyue_test_agent.application.skills import prepare_run_resources
from naiyue_test_agent.application.run_events import ApplicationEventSubscriptions, RunObservation
from naiyue_test_agent.application.session_controls import SessionControlMixin
from naiyue_test_agent.model_access import ModelCatalog
from .io_steps import IOCall, run_steps
from .async_session import AsyncSessionMixin
from naiyue_test_agent.agent_runtime.async_invocation import ainvoke_agent_run
from naiyue_test_agent.agent_runtime.resource_invocation import (
    ainvoke_agent_run_with_resources, aresume_agent_run_with_resources,
)


logger = logging.getLogger('naiyue_test_agent.application')


class SessionBusyError(RuntimeError):
    """A run is already active in this session."""


class SessionPendingApprovalError(RuntimeError):
    """A paused session needs its pending tool decisions before new input."""


class SessionManualReviewRequiredError(RuntimeError):
    """Uncertain tool side effects must be inspected before this session continues."""


def _pending_tool_approval(result, root: Path | None, policies: dict, *, context=None):
    interrupts = result.get('__interrupt__') if isinstance(result, dict) else None
    if not interrupts:
        return None
    if root is None or len(interrupts) != 1:
        raise RuntimeError('Unexpected tool approval interrupt')
    actions = interrupts[0].value.get('action_requests', [])
    messages = result.get('messages', [])
    last_ai = next((item for item in reversed(messages) if isinstance(item, AIMessage)), None)
    if last_ai is None:
        raise RuntimeError('Tool approval has no model tool calls')
    pending = []
    for call in last_ai.tool_calls:
        name = call['name']
        policy = policies.get(name)
        if policy is None:
            continue
        review = (policy.review_with_context(root, call.get('args'), context or {})
                  if isinstance(policy, ContextualApprovalPolicy) else policy.review(root, call.get('args')))
        if review is not None:
            if not isinstance(review, ApprovalReview):
                raise RuntimeError('Tool approval review is invalid')
            pending.append({'tool_call_id': call['id'], 'target': review.target,
                            'tool_name': name, 'args': call['args'],
                            'workspace': review.workspace,
                            'grant_value': review.grant_value})
    if (len(pending) != len(actions) or any(
        action.get('name') != item['tool_name'] or action.get('args') != item['args']
        for action, item in zip(actions, pending, strict=True)
    )):
        raise RuntimeError('Tool approval actions do not match model calls')
    return {'items': pending, 'message_count': len(messages)}


class SessionService(AsyncSessionMixin, SessionControlMixin):
    """Map public session IDs to private LangGraph threads and model choices."""

    _busy_error = SessionBusyError

    def __init__(self, catalog: ModelCatalog, approval_policies: dict | None = None):
        self._catalog = catalog
        self._approval_policies = approval_policies or {}
        self._sessions: dict[str, dict] = {}
        self._lock = Lock()
        self._model_selection_lock = Lock()
        self._events = ApplicationEventSubscriptions()
        self._active_runs = {}
        self._persistence = None

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
                'workspace_root': workspace_root,
                'pending_tool_approval': None,
                'pending_turn': None,
                'manual_review_required': False,
                'busy': False,
                'run_id': None,
                'run_project_instructions': None,
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
                'workspace_root': str(row['workspace_root']) if row['workspace_root'] else None,
                'busy': row['busy'], 'run_id': row['run_id'],
                'manual_review_required': row['manual_review_required'],
                'pending_approval': row['pending_tool_approval'] is not None,
                'approval_run_id': (row['pending_tool_approval'].get('requested_run_id')
                    if row['pending_tool_approval'] is not None else None)}

    def resolve_model(self, session_id: str):
        with self._lock:
            model_id = self._sessions[session_id]['model_id']
        return model_id, self._catalog.get(model_id)

    def resolve_project_instructions(self, session_id: str) -> str | None:
        with self._lock:
            return self._sessions[session_id]['run_project_instructions']

    def select_model(self, session_id: str, model_id: str) -> None:
        self._catalog.get(model_id)
        with self._model_selection_lock:
            with self._lock:
                self._sessions[session_id]
            if self._persistence is not None:
                self._persistence.storage.select_model(session_id, model_id)
            with self._lock:
                self._sessions[session_id]['model_id'] = model_id

    def run(self, agent, session_id: str, messages):
        return run_steps(self._run_steps(agent, session_id, messages))

    def _run_steps(self, agent, session_id, messages, *, run_id=None, async_mode=False, max_concurrency=4):
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
                self._events.publisher(session_id, run_id), 'message',
            )
            session['busy'] = True
            session['steering_open'] = True
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
            instructions, resources = yield IOCall(prepare_run_resources, (workspace_root,),
                {'enable_skills': 'read' in self._approval_policies})
            with self._lock:
                session['run_project_instructions'] = instructions
            observation.stage = 'execute'
            if workspace_root is not None:
                with observation.bind():
                    result = yield IOCall(invoke_agent_run_with_resources, (agent, messages),
                        dict(session_id=session_id, thread_id=thread_id, run_id=run_id,
                             workspace_root=workspace_root, read_resource_roots=resources),
                        async_call=partial(ainvoke_agent_run_with_resources, max_concurrency=max_concurrency),
                        critical=False)
            else:
                with observation.bind():
                    result = yield IOCall(invoke_agent_run, (agent, messages),
                        dict(session_id=session_id, thread_id=thread_id, run_id=run_id),
                        async_call=partial(ainvoke_agent_run, max_concurrency=max_concurrency), critical=False)
            observation.stage = 'finalize'
            pending = yield IOCall(_pending_tool_approval, (result, workspace_root, self._approval_policies),
                {'context': {'session_id': session_id, 'workspace_root': workspace_root,
                             'read_resource_roots': resources}})
            if pending is not None:
                with self._lock:
                    pending['requested_run_id'] = run_id
                    session['pending_tool_approval'] = pending
                observation.controls.approval_requested(tuple(item['tool_call_id'] for item in pending['items']))
                result['approval_requests'] = self._public_approvals(pending)
                self._record_approval('approval_requested', session_id, run_id,
                                      count=len(pending['items']))
            observation.result(result, pending)
            return result
        except BaseException as error:
            primary_error = error
            if async_mode and isinstance(error, asyncio.CancelledError):
                observation.cancelled(error)
            else:
                observation.failed(error)
            partial_tools = False
            if async_mode and isinstance(error, Exception) and not isinstance(error, GraphBubbleUp):
                partial_tools = yield IOCall(self._review_partial_state, (agent, session_id),
                                            async_call=self._areview_partial_state)
            if partial_tools or isinstance(error, (WriteOutcomeUnknown, BashOutcomeUnknown)) or (
                    async_mode and isinstance(error, asyncio.CancelledError)):
                with self._lock:
                    session['manual_review_required'] = True
                    session['pending_tool_approval'] = None
                event_key = ('session_event' if partial_tools or isinstance(error, asyncio.CancelledError)
                             else 'bash_event' if isinstance(error, BashOutcomeUnknown) else 'write_event')
                logger.error('session_manual_review_required', extra={event_key: {
                    'event': 'session_manual_review_required',
                    'session_id': session_id, 'run_id': run_id,
                }})
            raise
        finally:
            yield IOCall(self._finish_run, (session_id, run_id, session, agent, observation, primary_error),
                         async_call=self._afinish_run)

    @staticmethod
    def _public_approvals(pending):
        return [dict({'tool_call_id': item['tool_call_id'],
                      'tool_name': item['tool_name'], 'target': str(item['target'])},
                     **({'workspace': str(item['workspace'])}
                        if item['workspace'] is not None else {}))
                for item in pending['items']]

    @staticmethod
    def _record_approval(event: str, session_id: str, run_id: str, **counts) -> None:
        logger.info(event, extra={'approval_event': {
            'event': event, 'session_id': session_id, 'run_id': run_id, **counts,
        }})

    def pending_tool_approvals(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            pending = self._sessions[session_id]['pending_tool_approval']
            return self._public_approvals(pending) if pending is not None else []

    def resume_tool_approval(self, agent, session_id: str, decisions: list[str]):
        return run_steps(self._resume_steps(agent, session_id, decisions))

    def _resume_steps(self, agent, session_id, decisions, *, run_id=None, async_mode=False, max_concurrency=4):
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
            if (not isinstance(decisions, list) or len(decisions) != len(pending['items'])
                    or any(item not in ('approve', 'reject') for item in decisions)):
                raise ValueError('One approve/reject decision is required per pending call')
            run_id = run_id or uuid4().hex
            observation = RunObservation(
                self._events.publisher(session_id, run_id), 'approval_resume',
            )
            observation.turns.restore(session['pending_turn'])
            observation.controls.accept('approval',
                tool_call_ids=tuple(item['tool_call_id'] for item in pending['items']),
                decisions=tuple(decisions), origin_run_id=pending.get('requested_run_id'))
            session['busy'] = True
            session['steering_open'] = True
            session['stop_requested'] = False
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
            approved_by_tool: dict[str, dict[str, object]] = {}
            for item, decision in zip(pending['items'], decisions, strict=True):
                if decision == 'approve':
                    approved_by_tool.setdefault(item['tool_name'], {})[
                        item['tool_call_id']
                    ] = item['grant_value']
            grants = {
                name: self._approval_policies[name].grant_factory(values)
                for name, values in approved_by_tool.items()
            }

            instructions, resources = yield IOCall(prepare_run_resources, (workspace_root,),
                {'enable_skills': 'read' in self._approval_policies})
            with self._lock:
                session['run_project_instructions'] = instructions
            self._record_approval(
                'approval_review_submitted', session_id, run_id,
                approved_count=decisions.count('approve'),
                rejected_count=decisions.count('reject'),
            )
            observation.stage = 'execute'
            with observation.bind():
                result = yield IOCall(resume_agent_run_with_resources, (agent,),
                    dict(session_id=session_id, thread_id=thread_id, run_id=run_id, workspace_root=workspace_root,
                         decisions=decisions, approval_grants=grants, read_resource_roots=resources),
                    async_call=partial(aresume_agent_run_with_resources, max_concurrency=max_concurrency),
                    critical=False)
            observation.stage = 'finalize'
            next_pending = yield IOCall(_pending_tool_approval, (result, workspace_root, self._approval_policies),
                {'context': {'session_id': session_id, 'workspace_root': workspace_root,
                             'read_resource_roots': resources}})
            with self._lock:
                if next_pending is not None:
                    next_pending['requested_run_id'] = run_id
                session['pending_tool_approval'] = next_pending
            result['resume_start_index'] = pending['message_count']
            self._record_approval('approval_resumed', session_id, run_id,
                                  count=len(pending['items']))
            if next_pending is not None:
                result['approval_requests'] = self._public_approvals(next_pending)
                observation.controls.approval_requested(tuple(item['tool_call_id'] for item in next_pending['items']))
                self._record_approval('approval_requested', session_id, run_id,
                                      count=len(next_pending['items']))
            observation.result(result, next_pending)
            return result
        except BaseException as error:
            primary_error = error
            if async_mode and isinstance(error, asyncio.CancelledError):
                observation.cancelled(error)
            else:
                observation.failed(error)
            partial_tools = False
            if async_mode and isinstance(error, Exception) and not isinstance(error, GraphBubbleUp):
                partial_tools = yield IOCall(self._review_partial_state, (agent, session_id),
                                            async_call=self._areview_partial_state)
            if partial_tools or isinstance(error, (WriteOutcomeUnknown, BashOutcomeUnknown)) or (
                    async_mode and isinstance(error, asyncio.CancelledError)):
                with self._lock:
                    session['manual_review_required'] = True
                    session['pending_tool_approval'] = None
                event_key = ('session_event' if partial_tools or isinstance(error, asyncio.CancelledError)
                             else 'bash_event' if isinstance(error, BashOutcomeUnknown) else 'write_event')
                logger.error('session_manual_review_required', extra={event_key: {
                    'event': 'session_manual_review_required',
                    'session_id': session_id, 'run_id': run_id,
                }})
            raise
        finally:
            yield IOCall(self._finish_run, (session_id, run_id, session, agent, observation, primary_error),
                         async_call=self._afinish_run)


    def _cleanup_run(self, session_id, run_id, session, observation):
        with self._lock:
            review_required = session['manual_review_required']
            session['pending_turn'] = observation.turns.snapshot(
                pending=session['pending_tool_approval'] is not None)
            discarded = len(session['steering_queue'])
            session['steering_queue'].clear()
            discarded_follow_up = len(session['follow_up_queue'])
            session['follow_up_queue'].clear()
            observation.controls.close(observation.control_close_reason())
            self._active_runs.pop(session_id, None)
            session['steering_open'] = False
            session['stop_requested'] = False
            session['run_project_instructions'] = None
            if self._persistence is None:
                session['busy'] = False
                session['run_id'] = None
        if discarded:
            self._record_steering('steering_discarded', session_id, run_id, discarded)
        if discarded_follow_up:
            self._record_follow_up('follow_up_discarded', session_id, run_id, discarded_follow_up)
        return review_required

    def _finish_run(self, session_id, run_id, session, agent, observation, primary_error):
        secondary = None
        review_required = session['manual_review_required']
        try:
            review_required = self._cleanup_run(session_id, run_id, session, observation)
        except Exception as error:
            if primary_error is None:
                secondary = error
                observation.failed(error, stage='cleanup')
            else:
                logger.error('session_cleanup_failed error_type=%s', safe_error_type(error))
        try:
            if self._persistence is not None:
                self._persistence.finish(session_id, run_id, agent, observation)
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
                    session['run_project_instructions'] = None
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
