"""Saved approval facts, live child decisions and one-use grants."""

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4
from naiyue_test_agent.contracts.session_events import ApprovalItemData
from naiyue_test_agent.agent_runtime.observation import observe

from langchain_core.messages import AIMessage


from naiyue_test_agent.tools.permissions import OneUseApprovalGrants, permission_binding, remembered_rule, PermissionMechanismError


def pending_tool_approval(result):
    interrupts = result.get('__interrupt__') if isinstance(result, dict) else None
    if not interrupts:
        return None
    if len(interrupts) != 1:
        raise PermissionMechanismError('Unexpected tool approval interrupt')
    batch = result['tool_execution']
    calls = [call for call in batch['calls'] if call.get('decision') == 'ask']
    actions = interrupts[0].value.get('action_requests', [])
    if (len(calls) != len(actions) or any(action['name'] != call['name'] or action['args'] != call['args']
                                        for call, action in zip(calls, actions, strict=True))):
        raise PermissionMechanismError('Tool approval actions do not match the prepared calls')
    items = []
    for call in calls:
        subject = call['subject']
        items.append({'tool_call_id': call['id'], 'tool_name': call['name'],
                      'target': subject.get('resolved', subject.get('target', call['name'])),
                      'workspace': subject.get('workspace') if 'command' in call['args'] else None,
                      'prepared': call})
    return {'items': items, 'batch_id': batch['batch_id'], 'message_count': len(result['messages'])}


def validate_remembered(pending, decisions, remember_approved):
    remember = list(remember_approved or ())
    if any(not isinstance(identity, str) for identity in remember):
        raise ValueError('Remembered approvals require call identifiers')
    if len(set(remember)) != len(remember):
        raise ValueError('Duplicate remembered approval')
    approved = {item['tool_call_id']: item for item, decision in zip(pending['items'], decisions, strict=True)
                if decision == 'approve'}
    if any(identity not in approved for identity in remember):
        raise ValueError('Only approved calls may be remembered')
    return approved, remember


def validate_decisions(pending, decisions):
    if (not isinstance(decisions, list) or len(decisions) != len(pending['items'])
            or any(item not in ('approve', 'reject') for item in decisions)):
        raise ValueError('One approve/reject decision is required per pending call')


def prepare_resume(session_id, pending, decisions, remember_approved, *, agent_path='root'):
    validate_decisions(pending, decisions)
    approved, remember = validate_remembered(pending, decisions, remember_approved)
    bindings = {identity: permission_binding({'session_id': session_id, 'agent_path': agent_path},
                                            pending['batch_id'], item['prepared'])
                for identity, item in approved.items()}
    return OneUseApprovalGrants(bindings), [remembered_rule(approved[identity]['prepared']['subject']) for identity in remember]


def public_approvals(pending):
    return [dict({'tool_call_id': item['tool_call_id'], 'tool_name': item['tool_name'], 'target': str(item['target'])},
                 **({'workspace': str(item['workspace'])} if item['workspace'] is not None else {}))
            for item in pending['items']]


def root_approval_batch(session_id, pending):
    identity = hashlib.sha256(json.dumps([session_id, pending['batch_id'], pending['requested_run_id'],
        pending['items']], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    items = public_approvals(pending)
    for index, item in enumerate(items):
        item['approval_id'] = f'{identity}:{index}'
    return {'approval_batch_id': identity, 'kind': 'paused_root', 'agent_path': 'root', 'agent': None,
            'origin_run_id': pending['requested_run_id'], 'items': items}


def resolve_approval_rows(pending, approval_ids, decisions, remember_approved):
    if not isinstance(decisions, list):
        raise ValueError('Approval decisions require a complete list')
    by_id = {}
    for item in decisions:
        if (not isinstance(item, dict) or set(item) != {'approval_id', 'decision'}
                or not isinstance(item['approval_id'], str) or item['approval_id'] in by_id
                or item['decision'] not in ('approve', 'reject')):
            raise ValueError('Invalid or duplicate approval decision')
        by_id[item['approval_id']] = item['decision']
    if set(by_id) != set(approval_ids):
        raise ValueError('One decision is required per pending approval row')
    ordered = [by_id[identity] for identity in approval_ids]
    if not isinstance(remember_approved, (list, tuple)) or any(not isinstance(item, str) for item in remember_approved):
        raise ValueError('Remembered approvals require approval row identifiers')
    ids = dict(zip(approval_ids, (item['tool_call_id'] for item in pending['items']), strict=True))
    if any(identity not in ids for identity in remember_approved):
        raise ValueError('Only current approved calls may be remembered')
    remembered = [ids[identity] for identity in remember_approved]
    validate_remembered(pending, ordered, remembered)
    return ordered, remembered


APPROVAL_NODE = 'BatchApprovalMiddleware.after_model'


def is_pre_tool_approval(snapshot, messages):
    """Prove the fixed approval node paused before this batch executed tools."""
    if getattr(snapshot, 'next', ()) != (APPROVAL_NODE,) or len(snapshot.tasks) != 1 or not messages:
        return False
    task = snapshot.tasks[0]
    if (task.name != APPROVAL_NODE or task.path != ('__pregel_pull', APPROVAL_NODE)
            or task.state or task.error or task.result is not None or len(task.interrupts) != 1):
        return False
    latest = messages[-1]
    if not isinstance(latest, AIMessage) or not latest.tool_calls:
        return False
    try:
        batch = snapshot.values['tool_execution']
        calls = batch['calls']
        if batch['batch_id'] != latest.id or len(calls) != len(latest.tool_calls):
            return False
        if any((saved['id'], saved['name'], saved['args']) != (call['id'], call['name'], call['args'])
               for saved, call in zip(calls, latest.tool_calls, strict=True)):
            return False
        pending = pending_tool_approval({**snapshot.values, '__interrupt__': task.interrupts})
        return bool(pending and pending['items'])
    except (KeyError, TypeError, ValueError, AttributeError, PermissionMechanismError):
        return False


@dataclass
class PendingToolApproval:
    approval_batch_id: str
    session_id: str
    agent_path: str
    agent: str
    origin_run_id: str
    interrupt_id: str
    prepared: dict
    approval_ids: tuple[str, ...]
    future: asyncio.Future
    status: str = 'pending'
    decisions: tuple[str, ...] | None = None

    def public(self):
        items = public_approvals(self.prepared)
        for identity, item, saved in zip(self.approval_ids, items, self.prepared['items'], strict=True):
            item.update(approval_id=identity, execution_environment=deepcopy(saved['prepared']['subject']['execution_environment']))
        return {'approval_batch_id': self.approval_batch_id, 'kind': 'live_child',
                'agent_path': self.agent_path, 'agent': self.agent, 'origin_run_id': self.origin_run_id,
                'items': items}


def announce_approval(observation, batch):
    items = tuple(ApprovalItemData(**{key: row[key] for key in
        ('approval_id', 'tool_call_id', 'tool_name', 'target', 'workspace') if key in row}) for row in batch['items'])
    observe(observation.controls, observation.controls.approval_requested,
            tuple(row.tool_call_id for row in items), batch['approval_batch_id'], items)


class AgentSessionApproval:
    """One session owns pending child approvals and their live futures."""

    def pending_approval_batches(self):
        with self._lock:
            row = self._state
            batch = (root_approval_batch(self.session_id, row.pending_tool_approval)
                if row.pending_tool_approval is not None and self._run is None else None)
            batches = [batch] if batch is not None else []
            return batches + [pending.public() for instance in (self._run.subagent_instances.values() if self._run else ())
                if (pending := instance.get('approval')) is not None and pending.status == 'pending']

    def pending_subagent_approvals(self):
        return [batch for batch in self.pending_approval_batches() if batch['kind'] == 'live_child']

    def register_live_approval(self, instance, result, snapshot):
        messages = snapshot.values.get('messages', [])
        if not is_pre_tool_approval(snapshot, messages):
            raise PermissionMechanismError('Child approval is not a saved pre-tool approval')
        prepared = pending_tool_approval(result)
        saved = pending_tool_approval({**snapshot.values, '__interrupt__': snapshot.tasks[0].interrupts})
        if (prepared is None or prepared != saved
                or result['__interrupt__'][0].id != snapshot.tasks[0].interrupts[0].id):
            raise PermissionMechanismError('Child approval does not match its saved checkpoint')
        with self._lock:
            self._require_subagent(instance)
            run = self._run
            if (run is None or run.run_id != instance['root_run_id'] or instance.get('approval') is not None):
                raise PermissionMechanismError('Child approval requires an active unstopped instance')
            if run.stop_requested or instance['control'].requested or instance['status'] == 'stopping':
                return None
            future = asyncio.get_running_loop().create_future()
            pending = PendingToolApproval(uuid4().hex, instance['session_id'], instance['agent_path'],
                instance['role'], instance['root_run_id'], result['__interrupt__'][0].id,
                deepcopy(prepared), tuple(uuid4().hex for _ in prepared['items']), future)
            instance['approval'] = pending
            instance['status'] = 'awaiting_approval'
            instance['phase'] = 'awaiting'
        return pending

    def validate_approval_submission(self, approval_batch_id, origin_run_id, decisions, remember_approved):
        """Called under the session lock; accept only this live instance's batch."""
        session_id = self.session_id
        run = self._run
        if (not isinstance(approval_batch_id, str) or not isinstance(origin_run_id, str)
                or run is None or run.run_id != origin_run_id or run.stop_requested):
            raise ValueError('Approval targets a stale or stopped run')
        instance = next((instance for instance in (self._run.subagent_instances.values() if self._run else ())
                         if instance.get('approval') is not None
                         and instance['approval'].approval_batch_id == approval_batch_id), None)
        if instance is None:
            raise ValueError('Approval batch is no longer pending')
        pending = instance['approval']
        if (pending.status != 'pending' or pending.future.done() or instance['status'] != 'awaiting_approval'
                or instance['control'].requested or pending.origin_run_id != origin_run_id):
            raise ValueError('Approval batch is no longer pending')
        ordered, remembered = resolve_approval_rows(pending.prepared, pending.approval_ids, decisions, remember_approved)
        grants, rules = prepare_resume(session_id, pending.prepared, ordered, remembered, agent_path=pending.agent_path)
        return pending, ordered, grants, rules

    def submit_subagent_approval(self, *, approval_batch_id, decisions, origin_run_id, remember_approved=()):
        with self._lock:
            pending, ordered, grants, rules = self.validate_approval_submission(
                approval_batch_id, origin_run_id, decisions, remember_approved)
            pending.decisions = tuple(ordered)
            pending.status = 'accepted'
            for rule in rules:
                if rule not in self._state.session_allow:
                    self._state.session_allow.append(deepcopy(rule))
        pending.future.set_result((ordered, grants))
        return {'accepted': True, 'run_id': pending.origin_run_id}

    def release_live_approval(self, instance):
        with self._lock:
            self._require_subagent(instance)
            pending = instance.pop('approval', None)
            if pending is not None and pending.status == 'pending':
                pending.status = 'invalidated'
        if pending is not None and not pending.future.done():
            pending.future.cancel()
