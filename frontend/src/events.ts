import type { MessageRow, RuntimeEvent, RunView, ToolState } from './types';

const terminal = new Set(['completed', 'stopped', 'interrupted', 'failed']);

export function textContent(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(block => typeof block === 'string' ? block :
    block && block.type === 'text' && typeof block.text === 'string' ? block.text : '').join('');
  return '';
}

export function validateEvent(value: unknown): RuntimeEvent {
  const event = value as RuntimeEvent;
  if (!event || event.version !== 1 || !Number.isInteger(event.sequence) || event.sequence < 1 ||
      typeof event.session_id !== 'string' || typeof event.run_id !== 'string' ||
      typeof event.event_type !== 'string' || !event.data || typeof event.data !== 'object')
    throw new Error('invalid_event');
  if (event.event_type === 'run_finished' && !terminal.has(String(event.data.status)))
    throw new Error('invalid_event');
  return event;
}

export function applyEvent(view: RunView, event: RuntimeEvent): RunView {
  if ((view.run_id && event.run_id !== view.run_id) ||
      (view.session_id && event.session_id !== view.session_id)) return view;
  if (event.sequence <= view.last_sequence) return view;
  if (event.sequence !== view.last_sequence + 1) throw new Error('sequence_gap');
  let messages = view.messages;
  let tool_states = view.tool_states ?? {};
  const data = event.data;
  if (event.event_type === 'message_delta') {
    if (typeof data.message_id !== 'string' || typeof data.text !== 'string' ||
        !Number.isInteger(data.index) || Number(data.index) < 1) throw new Error('invalid_event');
    const old = messages.find(row => row.id === data.message_id);
    if (Number(data.index) !== 1 && Number(data.index) !== (old?.delta_index ?? 0) + 1)
      throw new Error('sequence_gap');
    const row: MessageRow = { id: data.message_id, type: 'ai',
      content: (Number(data.index) === 1 ? '' : old?.content ?? '') + data.text,
      provisional: true, state: 'draft', delta_index: Number(data.index), run_id: event.run_id };
    messages = old ? messages.map(item => item.id === row.id ? row : item) : [...messages, row];
  } else if (event.event_type === 'message_completed' || event.event_type === 'message_revised') {
    if (typeof data.snapshot_json !== 'string') throw new Error('invalid_event');
    const snapshot = JSON.parse(data.snapshot_json);
    if (typeof snapshot.id !== 'string' || typeof snapshot.type !== 'string') throw new Error('invalid_event');
    if (!snapshot.is_context_summary) {
      const row: MessageRow = { id: snapshot.id, type: snapshot.type, content: textContent(snapshot.content),
        ...(snapshot.tool_calls ? { tool_calls: snapshot.tool_calls } : {}),
        ...(snapshot.type === 'tool' ? { tool_call_id: snapshot.tool_call_id, status: snapshot.status, name: snapshot.name } : {}),
        state: 'complete', provisional: false, run_id: event.run_id };
      messages = messages.some(item => item.id === row.id)
        ? messages.map(item => item.id === row.id ? row : item) : [...messages, row];
    }
  } else if (event.event_type === 'tool_call_started' || event.event_type === 'tool_call_finished') {
    if (typeof data.tool_call_id !== 'string' || typeof data.boundary !== 'string') throw new Error('invalid_event');
    if (view.status === 'running') {
      const batch_id = [...messages].reverse().find(row => row.type === 'ai' &&
        row.tool_calls?.some(call => call.id === data.tool_call_id))?.id;
      const old = tool_states[data.tool_call_id];
      const finishing = event.event_type === 'tool_call_finished';
      const prior = finishing && old?.run_id === event.run_id && (!batch_id || old.batch_id === batch_id) ? old : {};
      tool_states = { ...tool_states, [data.tool_call_id]: { ...prior, ...data, run_id: event.run_id,
        batch_id: batch_id ?? (prior as ToolState).batch_id,
        state: finishing ? 'finished' : 'running',
        ...(finishing ? { finished_at: event.timestamp } : { started_at: event.timestamp }),
      } as ToolState };
    }
  } else if (event.event_type === 'tool_progress') {
    if (typeof data.tool_call_id !== 'string' || typeof data.batch_id !== 'string' ||
        typeof data.tool_name !== 'string' || typeof data.text !== 'string' ||
        typeof data.phase !== 'string' || !['queued', 'preparing', 'running', 'stopping', 'settling'].includes(String(data.activity)))
      throw new Error('invalid_event');
    const old = tool_states[data.tool_call_id];
    if (view.status === 'running' && old?.state === 'running' && old.run_id === event.run_id &&
        old.tool_name === data.tool_name && (!old.batch_id || old.batch_id === data.batch_id)) {
      tool_states = { ...tool_states, [data.tool_call_id]: { ...old,
        batch_id: data.batch_id, activity: data.activity, phase: data.phase, completed: data.completed,
        total: data.total, unit: data.unit, text: data.text, last_activity_at: event.timestamp } as ToolState };
    }
  } else if (event.event_type === 'control_accepted' && data.kind === 'stop') {
    return { ...view, stop_requested: true, last_sequence: event.sequence };
  } else if (event.event_type === 'run_finished') {
    messages = messages.map(row => row.provisional ? { ...row, state: 'unconfirmed' } : row);
    tool_states = Object.fromEntries(Object.entries(tool_states).map(([id, row]) =>
      [id, row.state === 'running' ? { ...row, state: 'unconfirmed', finished_at: event.timestamp } : row]));
    return { ...view, messages, tool_states, last_sequence: event.sequence, status: data.status as RunView['status'],
      manual_review_required: data.manual_review_required === true, failure: data.failure as RunView['failure'] };
  }
  return { ...view, messages, tool_states, last_sequence: event.sequence };
}

export function mergeMessages(previous: MessageRow[], incoming: MessageRow[]) {
  const updates = new Map(incoming.map(row => [row.id, { ...row, content: textContent(row.content) }]));
  const rows = previous.map(row => updates.get(row.id) ?? row);
  const ids = new Set(previous.map(row => row.id));
  return [...rows, ...incoming.filter(row => !ids.has(row.id)).map(row => updates.get(row.id)!)];
}
