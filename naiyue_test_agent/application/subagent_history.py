"""Read references derived from real parent calls, never caller-supplied thread IDs."""

import hashlib
import json
import re

from langchain_core.messages import AIMessage, ToolMessage


def subagent_identity(session_id, batch_id, tool_call_id):
    return hashlib.sha256(json.dumps([session_id, batch_id, tool_call_id]).encode()).hexdigest()


def subagent_target(session_id, batch_id, tool_call_id, agent_id, *, agent, owned_instances=None):
    if agent_id is None:
        return subagent_identity(session_id, batch_id, tool_call_id)
    if not isinstance(agent_id, str) or not re.fullmatch('[0-9a-f]{64}', agent_id):
        return None
    record = (owned_instances or {}).get(agent_id)
    if (record is None or record['session_id'] != session_id or record['agent'] != agent
            or record['agent_path'] != f'root/{agent}/{agent_id}'
            or record['child_thread_id'] != f'subagent:{agent_id}'):
        return None
    return agent_id


def subagent_records(session_id, messages, *, owned_instances=None):
    rows, batch = {}, {}
    for message in messages:
        if isinstance(message, AIMessage):
            batch = {}
            for call in message.tool_calls:
                args = call.get('args', {})
                if (call['name'] != 'subagent' or not message.id or not call.get('id')
                        or not isinstance(args.get('agent'), str) or not isinstance(args.get('task'), str)):
                    continue
                identity = subagent_target(session_id, message.id, call['id'], args.get('agent_id'),
                    agent=args['agent'], owned_instances=owned_instances)
                if identity is None:
                    continue
                row = dict(agent_id=identity, agent_path=f'root/{args["agent"]}/{identity}',
                           agent=args['agent'], task=args['task'], tool_call_id=call['id'],
                           parent_batch_id=message.id, status='unconfirmed')
                rows[identity] = row
                batch[call['id']] = row
        elif isinstance(message, ToolMessage) and message.tool_call_id in batch:
            batch[message.tool_call_id]['status'] = 'failed' if message.status == 'error' else 'completed'
        elif not isinstance(message, ToolMessage):
            batch = {}
    return list(rows.values())


def merge_registered_subagents(records, owned_instances):
    """List projection only; branch-reference queries must remain branch-specific."""
    rows = {row['agent_id']: row for row in records}
    for identity, record in owned_instances.items():
        if record['state'] == 'retired' and record['checkpoint_id'] is None:
            rows.pop(identity, None)
            continue
        status = {'ready': 'completed', 'failed': 'failed', 'stopped': 'stopped'}.get(record['state'], 'unconfirmed')
        if record['state'] == 'retired':
            status = rows.get(identity, {}).get('status', 'completed')
        rows[identity] = dict(agent_id=identity, agent_path=record['agent_path'], agent=record['agent'],
            task=record['last_task'], tool_call_id=record['last_parent_tool_call_id'],
            parent_batch_id=record['last_parent_batch_id'], status=status)
    return list(rows.values())
