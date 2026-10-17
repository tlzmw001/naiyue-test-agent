"""Project saved permission facts into official approvals and one-use grants."""

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
    if len(set(remember)) != len(remember):
        raise ValueError('Duplicate remembered approval')
    approved = {item['tool_call_id']: item for item, decision in zip(pending['items'], decisions, strict=True)
                if decision == 'approve'}
    if any(identity not in approved for identity in remember):
        raise ValueError('Only approved calls may be remembered')
    return approved, remember


def prepare_resume(session_id, pending, decisions, remember_approved):
    approved, remember = validate_remembered(pending, decisions, remember_approved)
    bindings = {identity: permission_binding({'session_id': session_id, 'agent_path': 'root'},
                                            pending['batch_id'], item['prepared'])
                for identity, item in approved.items()}
    return OneUseApprovalGrants(bindings), [remembered_rule(approved[identity]['prepared']['subject']) for identity in remember]


def public_approvals(pending):
    return [dict({'tool_call_id': item['tool_call_id'], 'tool_name': item['tool_name'], 'target': str(item['target'])},
                 **({'workspace': str(item['workspace'])} if item['workspace'] is not None else {}))
            for item in pending['items']]


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
