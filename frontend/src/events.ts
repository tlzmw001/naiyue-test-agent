import type { ContextSnapshot } from './contextTypes';
import type { MessageRow, RuntimeEvent, RunView, ToolState } from './types';
import { reasoningDelta } from './reasoning';
import { applyAgentEvent, textContent } from './agentEvents';

const terminal = new Set(['completed', 'stopped', 'interrupted', 'failed']);

export function validateEvent(value: unknown): RuntimeEvent {
  const event = value as RuntimeEvent;
  if (!event || event.version !== 1 || !Number.isInteger(event.sequence) || event.sequence < 1 ||
      typeof event.session_id !== 'string' || typeof event.run_id !== 'string' ||
      typeof event.event_type !== 'string' || !event.data || typeof event.data !== 'object')
    throw new Error('invalid_event');
  if (event.event_type === 'run_finished' && !terminal.has(String(event.data.status)))
    throw new Error('invalid_event');
  if (event.source != null && (['agent_path', 'agent_id', 'agent_run_id', 'parent_run_id', 'parent_tool_call_id']
    .some(key => typeof event.source![key as keyof typeof event.source] !== 'string' || !event.source![key as keyof typeof event.source]) ||
    !event.source.agent_path.startsWith('root/') || event.source.parent_run_id !== event.run_id ||
    event.source.agent_path.split('/').at(-1) !== event.source.agent_id)) throw new Error('invalid_event');
  return event;
}

export function applyEvent(view: RunView, event: RuntimeEvent): RunView {
  if ((view.run_id && event.run_id !== view.run_id) ||
      (view.session_id && event.session_id !== view.session_id)) return view;
  if (event.sequence <= view.last_sequence) return view;
  if (event.sequence !== view.last_sequence + 1) throw new Error('sequence_gap');
  if (event.source) {
    const batch = [...view.messages].reverse().find(row => row.type === 'ai' &&
      row.tool_calls?.some(call => call.id === event.source!.parent_tool_call_id))?.id;
    return { ...view, last_sequence: event.sequence, agents: { ...view.agents,
      [event.source.agent_path]: applyAgentEvent(view.agents?.[event.source.agent_path], event, batch) } };
  }
  let messages = view.messages;
  let tool_states = view.tool_states ?? {};
  const data = event.data;
  if (event.event_type === 'run_started') {
    return { ...view, mode: data.mode as RunView['mode'], last_sequence: event.sequence };
  } else if (event.event_type === 'compaction_finished' && data.trigger === 'manual') {
    return { ...view, compaction_result: { status: String(data.status), reason: data.reason as string | null }, last_sequence: event.sequence };
  } else if (event.event_type === 'model_context_snapshot') {
    if (data.snapshot_json === null) return { ...view, context_snapshot: null, last_sequence: event.sequence };
    if (typeof data.snapshot_json !== 'string') throw new Error('invalid_event');
    const context_snapshot = JSON.parse(data.snapshot_json) as ContextSnapshot;
    if (context_snapshot.version !== 1 || context_snapshot.model_call_id !== data.model_call_id) throw new Error('invalid_event');
    return { ...view, context_snapshot, last_sequence: event.sequence };
  } else if (event.event_type === 'message_reasoning_delta') {
    messages = reasoningDelta(messages, data, event.run_id);
  } else if (event.event_type === 'message_delta') {
    if (typeof data.message_id !== 'string' || typeof data.text !== 'string' ||
        !Number.isInteger(data.index) || Number(data.index) < 1) throw new Error('invalid_event');
    const old = messages.find(row => row.id === data.message_id);
    if (Number(data.index) !== 1 && Number(data.index) !== (old?.delta_index ?? 0) + 1)
      throw new Error('sequence_gap');
    const unavailable = Number(data.index) !== 1 && old?.content_unavailable === true;
    const row: MessageRow = { ...old, id: data.message_id, type: 'ai', reply_id: data.reply_id as string | null,
      content: unavailable ? old!.content : (Number(data.index) === 1 ? '' : old?.content ?? '') + data.text,
      ...(unavailable ? { content_unavailable: true, content_truncated: true } : {}),
      provisional: true, state: 'draft', delta_index: Number(data.index), run_id: event.run_id };
    messages = old ? messages.map(item => item.id === row.id ? row : item) : [...messages, row];
  } else if (event.event_type === 'message_completed' || event.event_type === 'message_revised') {
    if (typeof data.snapshot_json !== 'string') throw new Error('invalid_event');
    const snapshot = JSON.parse(data.snapshot_json);
    if (typeof snapshot.id !== 'string' || typeof snapshot.type !== 'string') throw new Error('invalid_event');
    if (!snapshot.is_context_summary) {
      const row: MessageRow = { ...snapshot, id: snapshot.id, type: snapshot.type, content: textContent(snapshot.content),
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

export function mergeMessages(previous: MessageRow[], incoming: MessageRow[], prepend = false) {
  const values = new Map(previous.map(row => [row.id, row]));
  for (const item of incoming) {
    const row = { ...item, content: textContent(item.content) }, old = values.get(row.id);
    if (old && ((old.revision != null && row.revision != null && old.revision > row.revision) ||
        (!old.provisional && row.provisional) || (old.provisional && row.provisional && old.run_id === row.run_id &&
          (old.delta_index ?? 0) > (row.delta_index ?? 0)))) continue;
    const preview = row.content_truncated || row.content_unavailable;
    if (old && !old.content_truncated && !old.content_unavailable && !old.provisional && preview &&
        (old.revision == null || row.revision == null || row.revision <= old.revision)) {
      values.set(row.id, { ...row, ...old });
    } else values.set(row.id, { ...row,
      // A saved preview can arrive before full-body loading. Keep the verified
      // streamed prefix visible, while its preview flag still requests calibration.
      ...(preview && old?.provisional && !old.content_unavailable &&
        (old.revision == null || row.revision == null || old.revision === row.revision) &&
        (!old.run_id || !row.run_id || old.run_id === row.run_id) &&
        old.content.length > row.content.length && old.content.startsWith(row.content) ? { content: old.content } : {}),
      ...(old?.sequence != null && row.sequence == null ? { sequence: old.sequence } : {}),
      ...(old?.revision != null && row.revision == null ? { revision: old.revision } : {}),
      ...(old?.detail && !row.detail ? { detail: old.detail } : {}),
      ...(old?.parent_message_id && !row.parent_message_id ? { parent_message_id: old.parent_message_id } : {}),
    });
  }
  const ids = [...new Set(previous.map(row => row.id))];
  let prefix = 0;
  incoming.forEach((row, index) => {
    if (ids.includes(row.id)) return;
    let position = -1;
    if (row.sequence != null) {
      position = ids.findIndex(id => values.get(id)!.sequence != null && values.get(id)!.sequence! > row.sequence!);
      if (position < 0) {
        let last = ids.length - 1;
        while (last >= 0 && values.get(ids[last])!.sequence == null) last--;
        if (last >= 0) position = last + 1;
      }
    }
    if (position < 0) {
      const next = incoming.slice(index + 1).find(item => ids.includes(item.id));
      position = next ? ids.indexOf(next.id) : prepend ? prefix++ : ids.length;
    }
    ids.splice(position, 0, row.id);
  });
  return ids.map(id => values.get(id)!);
}
