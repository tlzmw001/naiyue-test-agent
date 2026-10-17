"""Read references derived from real parent calls, never caller-supplied thread IDs."""

import hashlib
import json

from langchain_core.messages import AIMessage, ToolMessage


def subagent_identity(session_id, batch_id, tool_call_id):
    return hashlib.sha256(json.dumps([session_id, batch_id, tool_call_id]).encode()).hexdigest()


def subagent_records(session_id, messages):
    rows, batch = [], {}
    for message in messages:
        if isinstance(message, AIMessage):
            batch = {}
            for call in message.tool_calls:
                args = call.get('args', {})
                if (call['name'] != 'subagent' or not message.id or not call.get('id')
                        or not isinstance(args.get('agent'), str) or not isinstance(args.get('task'), str)):
                    continue
                identity = subagent_identity(session_id, message.id, call['id'])
                row = dict(agent_id=identity, agent_path=f'root/{args["agent"]}/{identity}',
                           agent=args['agent'], task=args['task'], tool_call_id=call['id'],
                           parent_batch_id=message.id, status='unconfirmed')
                rows.append(row)
                batch[call['id']] = row
        elif isinstance(message, ToolMessage) and message.tool_call_id in batch:
            batch[message.tool_call_id]['status'] = 'failed' if message.status == 'error' else 'completed'
        elif not isinstance(message, ToolMessage):
            batch = {}
    return rows
