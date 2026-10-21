"""One logical session's state, configuration and controlled transitions."""

from copy import deepcopy
from threading import Lock

from naiyue_test_agent.agent_runtime.observation import observe
from .errors import RunCapacityExceededError, SessionBusyError, SessionManualReviewRequiredError, SessionPendingApprovalError
from .session_approval import (
    AgentSessionApproval,
    public_approvals, resolve_approval_rows, root_approval_batch,
    validate_decisions, validate_remembered,
)
from .session_controls import AgentSessionControls
from .session_execution import SessionExecution
from .session_state import ActiveRun, SessionState
from .session_subagents import AgentSessionSubagents


class AgentSession(AgentSessionControls, SessionExecution, AgentSessionSubagents, AgentSessionApproval):
    """Borrow shared dependencies; own exactly one State and its short locks."""

    def __init__(self, session_id, state: SessionState, *, catalog, tool_registry,
                 permission_settings, persistence=None, invoke_subagent=None, registered_subagents=None,
                 progress_settings=None, subagent_result_settings=None, mcp_resources=None):
        self.session_id = session_id
        self._state = state
        self._catalog = catalog
        self._tool_registry = tool_registry
        self._permission_settings = permission_settings
        self._persistence = persistence
        self._run: ActiveRun | None = None
        self._invoke_subagent = invoke_subagent
        self._registered_subagents = registered_subagents
        self._progress_settings = progress_settings
        self._subagent_result_settings = subagent_result_settings
        self._mcp_resources = mcp_resources
        self._lock = Lock()
        self._model_selection_lock = Lock()
        self._tool_selection_lock = Lock()
        self._permission_selection_lock = Lock()
        if self._mcp_resources is not None:
            self._mcp_resources.set_session_occupied(session_id, state.pending_tool_approval is not None)

    def describe_session(self):
        with self._lock:
            row = self._state
            return {'session_id': self.session_id, 'conversation_revision': row.conversation_revision, 'model_id': row.model_id,
                'thinking_level': row.thinking_level,
                'active_tools': list(row.active_tool_names), 'permission_mode': row.permission_mode,
                'workspace_root': str(row.workspace_root) if row.workspace_root else None,
                'busy': (self._run is not None), 'run_id': (self._run.run_id if self._run else None),
                'manual_review_required': row.manual_review_required,
                'pending_approval': row.pending_tool_approval is not None,
                'approval_run_id': (row.pending_tool_approval.get('requested_run_id')
                    if row.pending_tool_approval is not None else None)}

    def activate_regenerated_thread(self, run, thread_id, revision):
        with self._lock:
            self._require_run(run)
            self._state.thread_id = thread_id
            self._state.conversation_revision = revision

    def get_thread_id(self):
        with self._lock:
            return self._state.thread_id

    def is_busy(self):
        with self._lock:
            return self._run is not None

    def snapshot_for_finish(self):
        with self._lock:
            return {'thread_id': self._state.thread_id,
                    'manual_review_required': self._state.manual_review_required}

    def snapshot_for_context(self):
        with self._lock:
            row = self._state
            return {'thread_id': row.thread_id, 'model_id': row.model_id,
                'workspace_root': row.workspace_root, 'active_tool_names': row.active_tool_names,
                'busy': (self._run is not None), 'pending_approval': row.pending_tool_approval is not None,
                'manual_review_required': row.manual_review_required}

    def context_snapshot_is_current(self, snapshot):
        with self._lock:
            row = self._state
            return (self._run is None and not row.pending_tool_approval and not row.manual_review_required
                and row.thread_id == snapshot['thread_id'] and row.model_id == snapshot['model_id']
                and row.active_tool_names == snapshot['active_tool_names'])

    def approval_seed_identity(self):
        with self._lock:
            row = self._state
            return ((row.thread_id, row.pending_tool_approval['batch_id'])
                    if row.pending_tool_approval is not None else None)

    def snapshot_for_run(self):
        with self._lock:
            row = self._state
            return {'thread_id': row.thread_id, 'workspace_root': row.workspace_root,
                'model_id': row.model_id, 'busy': (self._run is not None), 'run_id': (self._run.run_id if self._run else None),
                'stop_requested': (self._run.stop_requested if self._run else False), 'steering_open': (self._run.steering_open if self._run else False),
                'pending': deepcopy(row.pending_tool_approval),
                'pending_turn': deepcopy(row.pending_turn)}

    def select_model(self, model_id):
        self._catalog.get(model_id)
        with self._model_selection_lock:
            if self._persistence is not None:
                self._persistence.storage.select_model(self.session_id, model_id)
            with self._lock:
                self._state.model_id = model_id
                self._state.thinking_level = 'model_default'

    def select_thinking(self, model_id, level):
        self._catalog.with_thinking(model_id, level)
        with self._model_selection_lock:
            with self._lock:
                if self._state.model_id != model_id:
                    raise ValueError('Model selection changed')
            if self._persistence is not None:
                self._persistence.storage.select_thinking(self.session_id, level)
            with self._lock:
                self._state.thinking_level = level

    def set_active_tools(self, names):
        names = tuple(spec.tool.name for spec in self._tool_registry.select(names))
        with self._tool_selection_lock:
            if self._persistence is not None:
                self._persistence.storage.select_active_tools(self.session_id, names)
            with self._lock:
                self._state.active_tool_names = names

    def set_permission_mode(self, mode):
        self._permission_settings.validate_mode(mode)
        with self._permission_selection_lock:
            if self._persistence is not None:
                self._persistence.storage.select_permission_mode(self.session_id, mode)
            with self._lock:
                self._state.permission_mode = mode

    def resolve_model(self, agent_path='root'):
        with self._lock:
            if agent_path == 'root':
                identity, level = self._state.model_id, self._state.thinking_level
            else:
                identity, level = self._run.subagent_instances[agent_path]['model_id'], 'model_default'
        return identity, self._catalog.with_thinking(identity, level)

    def resolve_request_configuration(self, agent_path='root'):
        with self._lock:
            if agent_path == 'root':
                row = self._state
                identity, level, names = row.model_id, row.thinking_level, row.active_tool_names
            else:
                instance = self._run.subagent_instances[agent_path]
                identity, level, names = instance['model_id'], 'model_default', instance['tool_names']
        return identity, self._catalog.with_thinking(identity, level), tuple(names)

    def get_active_tools(self, agent_path='root'):
        with self._lock:
            return (self._state.active_tool_names if agent_path == 'root'
                else self._run.subagent_instances[agent_path]['tool_names'])

    def get_permission_mode(self):
        with self._lock:
            return self._state.permission_mode

    def resolve_permission_context(self, agent_path='root'):
        with self._lock:
            if agent_path != 'root':
                self._run.subagent_instances[agent_path]
            return {'mode': self._state.permission_mode,
                'session_allow': deepcopy(self._state.session_allow),
                'project_instructions': (self._run.project_instructions if self._run else None)}

    def resolve_prompt_parts(self):
        with self._lock:
            return deepcopy((self._run.prompt_parts if self._run else []))

    def admit_run(self, run_id, mode, observation, *, decisions=None, remember_approved=None, stop=False, capacity_available=True, expected_conversation_revision=None):
        resuming = mode == 'approval_resume'
        with self._lock:
            row = self._state
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
            if self._run is not None:
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
            run = ActiveRun(run_id, observation, steering_open=mode != 'compact' and not stop,
                            stop_requested=stop)
            self._run = run
            if self._mcp_resources is not None:
                self._mcp_resources.set_session_occupied(self.session_id, True)
            return run

    def _require_run(self, run):
        # Called with the state lock held. Identity prevents same-ID stale cleanup.
        if self._run is not run or run is None:
            raise SessionBusyError('State transition targets a different run')

    def freeze_run_resources(self, run, prompt_parts, project_instructions):
        with self._lock:
            self._require_run(run)
            run.prompt_parts = deepcopy(prompt_parts)
            run.project_instructions = project_instructions

    def replace_pending_approval(self, run, pending):
        with self._lock:
            self._require_run(run)
            prepared = deepcopy(pending)
            if prepared is not None:
                prepared['requested_run_id'] = run.run_id
            self._state.pending_tool_approval = prepared

    def remember_approved_rules(self, run, rules):
        with self._lock:
            self._require_run(run)
            for rule in rules:
                if rule not in self._state.session_allow:
                    self._state.session_allow.append(deepcopy(rule))

    def mark_manual_review(self, run):
        with self._lock:
            self._require_run(run)
            self._state.manual_review_required = True
            self._state.pending_tool_approval = None

    def pending_tool_approvals(self):
        with self._lock:
            pending = self._state.pending_tool_approval
            return public_approvals(pending) if pending is not None else []

    def pending_root_approval_batch(self):
        with self._lock:
            row = self._state
            return (root_approval_batch(self.session_id, row.pending_tool_approval)
                    if row.pending_tool_approval is not None and self._run is None else None)

    def prepare_root_approval_submission(self, *, approval_batch_id, origin_run_id, decisions, remember_approved=()):
        with self._lock:
            row = self._state
            pending = row.pending_tool_approval
            if pending is None or (self._run is not None) or row.manual_review_required:
                raise ValueError('Root approval is no longer pending')
            batch = root_approval_batch(self.session_id, pending)
            if batch['approval_batch_id'] != approval_batch_id or batch['origin_run_id'] != origin_run_id:
                raise ValueError('Root approval targets a stale batch')
            return resolve_approval_rows(pending, [item['approval_id'] for item in batch['items']], decisions, remember_approved)

    def validate_root_approval_stop(self, *, approval_batch_id, origin_run_id):
        with self._lock:
            row = self._state
            pending = row.pending_tool_approval
            if pending is None or (self._run is not None) or row.manual_review_required:
                raise ValueError('Root approval is no longer pending')
            batch = root_approval_batch(self.session_id, pending)
            if batch['approval_batch_id'] != approval_batch_id or batch['origin_run_id'] != origin_run_id:
                raise ValueError('Root approval targets a stale batch')

    def close_control_window(self, run):
        with self._lock:
            self._require_run(run)
            row, observation = self._state, run.observation
            row.pending_turn = observe(observation.turns, observation.turns.snapshot,
                pending=row.pending_tool_approval is not None)
            discarded, follow_up = len(run.steering_queue), len(run.follow_up_queue)
            run.steering_queue.clear()
            run.follow_up_queue.clear()
            observe(observation.controls, observation.controls.close, observation.control_close_reason())
            run.controls_open = run.steering_open = False
            run.stop_requested = False
            run.prompt_parts, run.project_instructions = [], None
            return row.manual_review_required, discarded, follow_up

    def release_run(self, run):
        with self._lock:
            self._require_run(run)
            if run.controls_open or run.subagent_instances:
                raise SessionBusyError('Run cleanup has not settled')
            self._run = None
            if self._mcp_resources is not None:
                self._mcp_resources.set_session_occupied(self.session_id, self._state.pending_tool_approval is not None)
