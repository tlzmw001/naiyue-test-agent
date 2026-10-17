"""Project saved permission facts into official approvals and one-use grants."""

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
