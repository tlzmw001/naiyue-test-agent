"""Read references derived from real parent calls, never caller-supplied thread IDs."""

from naiyue_test_agent.contracts.subagent_identity import subagent_target

from langchain_core.messages import AIMessage, ToolMessage






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
