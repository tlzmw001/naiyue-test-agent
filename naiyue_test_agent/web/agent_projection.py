"""Small live child views, separate from the root conversation projection."""

import json
from copy import deepcopy
from naiyue_test_agent.model_access.messages import content_text
from .serialization import encoded_size


def summarize_agent(agent, draft_budget):
    """Root controls need identity/status and a bounded delta baseline, never tool bodies."""
    value = {key: deepcopy(item) for key, item in agent.items() if key not in ('messages', 'tool_states')}
    value['tool_states'] = {identity: {key: deepcopy(item) for key, item in row.items()
                                     if key not in ('args', 'result', 'text')}
                           for identity, row in agent['tool_states'].items()
                           if row.get('batch_id') == agent.get('batch_id')}
    drafts = [row for row in agent['messages'] if row['provisional']]
    if encoded_size(drafts) <= draft_budget:
        value['messages'] = deepcopy(drafts)
    else:
        value['messages'] = [{key: row[key] for key in ('id', 'type', 'state', 'provisional', 'delta_index')}
                             | {'content': '', 'content_unavailable': True} for row in drafts]
    return value


def observe_agent(agents, event, parent_messages=()):
    source, kind, data = event['source'], event['event_type'], event['data']
    path = source['agent_path']
    agent = agents.setdefault(path, {'source': source, 'status': 'running', 'tool_states': {}, 'messages': []})
    agent['source'] = source
    if not agent.get('parent_batch_id'):
        agent['parent_batch_id'] = next((row['id'] for row in reversed(parent_messages)
            if row['type'] == 'ai' and any(call['id'] == source['parent_tool_call_id']
                for call in row.get('tool_calls', []))), None)
    states = agent['tool_states']
    messages = agent['messages']
    if kind == 'run_started':
        if agent['status'] != 'stopping':
            agent['status'] = 'running'
    elif kind == 'control_accepted' and data.get('kind') == 'stop':
        agent['status'] = 'stopping'
    elif kind == 'approval_requested':
        agent['status'] = 'awaiting_approval'
    elif kind == 'run_finished':
        agent['status'] = 'awaiting_approval' if data['status'] == 'interrupted' else data['status']
        for row in messages:
            if row['provisional']:
                row['state'] = 'unconfirmed'
        if data['status'] != 'interrupted':
            for row in states.values():
                if row['state'] == 'running':
                    row.update(state='unconfirmed', finished_at=event['timestamp'])
    elif kind == 'message_reasoning_delta':
        from naiyue_test_agent.application.stream_projection import reasoning_draft
        old = next((row for row in messages if row['id'] == data['message_id']), None)
        row = reasoning_draft(old, message_id=data['message_id'], text=data['text'], index=data['index'],
                              reply_id=data.get('reply_id'), run_id=source['agent_run_id'])
        if old is None:
            messages.append(row)
        else:
            old.update(row)
    elif kind == 'message_delta':
        old = next((row for row in messages if row['id'] == data['message_id']), None)
        if data['index'] != 1 and data['index'] != (old or {}).get('delta_index', 0) + 1:
            raise ValueError('Child message delta gap')
        row = dict(id=data['message_id'], type='ai', reply_id=data.get('reply_id'),
            content=('' if data['index'] == 1 else old['content']) + data['text'],
            provisional=True, state='draft', delta_index=data['index'], run_id=source['agent_run_id'])
        if old is None:
            messages.append(row)
        else:
            old.update(row)
    elif kind in ('message_completed', 'message_revised'):
        message = json.loads(data['snapshot_json'])
        if message.get('is_context_summary'):
            return
        row = {**message, 'content': content_text(message['content']), 'provisional': False,
               'state': 'complete', 'run_id': source['agent_run_id']}
        old = next((item for item in messages if item['id'] == message['id']), None)
        if old is None:
            messages.append(row)
        else:
            old.clear()
            old.update(row)
        if message['type'] == 'ai' and message.get('tool_calls'):
            agent['batch_id'] = message['id']
            for call in message['tool_calls']:
                prior = states.get(call['id'], {})
                if prior.get('batch_id') != message['id']:
                    prior = {}
                states[call['id']] = {**prior, 'tool_call_id': call['id'],
                    'tool_name': call['name'], 'args': call['args'], 'batch_id': message['id'],
                    'run_id': source['agent_run_id'], 'state': prior.get('state', 'pending')}
        elif message['type'] == 'tool' and message['tool_call_id'] in states:
            states[message['tool_call_id']]['result'] = {'id': message['id'], 'type': 'tool',
                'content': content_text(message['content']), 'tool_call_id': message['tool_call_id'],
                'status': message.get('status', 'success'), 'state': 'complete', 'provisional': False}
    elif kind in ('tool_call_started', 'tool_call_finished', 'tool_progress'):
        call_id = data['tool_call_id']
        old = states.get(call_id, {})
        if kind == 'tool_progress':
            if (old.get('state') == 'running' and old.get('run_id') == source['agent_run_id']
                    and old.get('tool_name') == data['tool_name'] and old.get('batch_id') in (None, data['batch_id'])):
                old.update(data, last_activity_at=event['timestamp'])
        else:
            finishing = kind == 'tool_call_finished'
            states[call_id] = {**old, **data, 'run_id': source['agent_run_id'],
                'state': 'finished' if finishing else 'running',
                'finished_at' if finishing else 'started_at': event['timestamp']}
