"""Presentation state keeps provisional drafts separate from final snapshots."""

import json
from copy import deepcopy


def reasoning_draft(old, *, message_id, text, index, reply_id, run_id):
    if index != 1 and index != (old or {}).get('reasoning_index', 0) + 1:
        raise ValueError('reasoning_delta_gap')
    return {**(old or {}), 'id': message_id, 'type': 'ai', 'content': (old or {}).get('content', ''),
        'reasoning': ('' if index == 1 else (old or {}).get('reasoning', '')) + text,
        'reasoning_index': index, 'reply_id': reply_id, 'run_id': run_id, 'provisional': True, 'state': 'draft'}


class StreamingMessageProjection:
    def __init__(self):
        self._messages = {}
        self._sequences = {}
        self.run_status = {}
        self.incomplete = False

    @property
    def messages(self):
        return list(self._messages.values())

    def seed(self, session_id, rows):
        """Seed existing presentation/checkpoint rows before observing a new run."""
        for row in rows:
            self._messages[(session_id, row['id'])] = {
                **deepcopy(row), 'session_id': session_id, 'run_id': row.get('run_id')}

    def __call__(self, event):
        previous = self._sequences.get(event.run_id, 0)
        if event.sequence <= previous:
            return
        if event.sequence != previous + 1:
            self.incomplete = True
        self._sequences[event.run_id] = event.sequence
        if event.source is not None:
            return
        data = event.data
        if event.event_type == 'message_reasoning_delta':
            key = (event.session_id, data.message_id)
            self._messages[key] = reasoning_draft(self._messages.get(key), message_id=data.message_id,
                text=data.text, index=data.index, reply_id=data.reply_id, run_id=event.run_id)
        elif event.event_type == 'message_delta':
            key = (event.session_id, data.message_id)
            if data.index == 1:
                self._messages[key] = dict(**{k: v for k, v in self._messages.get(key, {}).items() if k in ('reasoning', 'reasoning_index')}, id=data.message_id, type='ai', content='', provisional=True,
                    state='draft', reply_id=data.reply_id, session_id=event.session_id, run_id=event.run_id, delta_index=0)
            row = self._messages.get(key)
            if row is None or data.index != row.get('delta_index', 0) + 1:
                self.incomplete = True
                return
            row['content'] += data.text
            row['delta_index'] = data.index
        elif event.event_type in ('message_completed', 'message_revised'):
            row = json.loads(data.snapshot_json)
            if row['is_context_summary']:
                return
            row.update(provisional=False, state='complete', visibility=data.visibility,
                       session_id=event.session_id, run_id=event.run_id)
            if row['type'] == 'tool':
                parent = next((m for m in reversed(self.messages) if m['type'] != 'tool'), None)
                row['parent_message_id'] = (parent['id'] if parent and any(
                    c['id'] == row['tool_call_id'] for c in parent.get('tool_calls', [])) else None)
            self._messages[(event.session_id, row['id'])] = row
        elif event.event_type == 'message_snapshot_unavailable':
            self.incomplete = True
        elif event.event_type == 'run_finished':
            self.run_status[event.run_id] = data.status
            for row in self._messages.values():
                if row['run_id'] == event.run_id and row['provisional']:
                    row['state'] = 'unconfirmed'
        # Context removals change model input, not the live presentation history.
