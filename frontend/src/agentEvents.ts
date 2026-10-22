import { reasoningDelta } from './reasoning';
import type { AgentView, MessageRow, RuntimeEvent, ToolState } from './types';

export function textContent(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(block => typeof block === 'string' ? block :
    block && block.type === 'text' && typeof block.text === 'string' ? block.text : '').join('');
  return '';
}

export function applyAgentEvent(previous: AgentView | undefined, event: RuntimeEvent, parentBatchId?: string): AgentView {
  const source = event.source!;
  const kind = event.event_type, data = event.data;
  if (previous && previous.source.agent_run_id !== source.agent_run_id && kind !== 'run_started') return previous;
  if (kind === 'run_started' && data.mode === 'message') previous = undefined;
  const agent: AgentView = { ...previous, source, status: previous?.status ?? 'running',
    parent_batch_id: previous?.parent_batch_id ?? parentBatchId,
    messages: [...(previous?.messages ?? [])],
    tool_states: Object.assign(Object.create(null), previous?.tool_states) };
  if (kind === 'run_started' && agent.status !== 'stopping') agent.status = 'running';
  else if (kind === 'control_accepted' && data.kind === 'stop') agent.status = 'stopping';
  else if (kind === 'approval_requested') agent.status = 'awaiting_approval';
  else if (kind === 'run_finished') {
    agent.status = data.status === 'interrupted' ? 'awaiting_approval' : data.status as AgentView['status'];
    agent.messages = agent.messages!.map(row => row.provisional ? { ...row, state: 'unconfirmed' } : row);
    if (data.status !== 'interrupted') agent.tool_states = Object.fromEntries(Object.entries(agent.tool_states).map(([id, row]) =>
      [id, row.state === 'running' ? { ...row, state: 'unconfirmed', finished_at: event.timestamp } : row]));
  } else if (kind === 'message_reasoning_delta') {
    agent.messages = reasoningDelta(agent.messages!, data, source.agent_run_id);
  } else if (kind === 'message_delta') {
    if (typeof data.message_id !== 'string' || typeof data.text !== 'string' ||
        !Number.isInteger(data.index) || Number(data.index) < 1) throw new Error('invalid_event');
    const old = agent.messages!.find(row => row.id === data.message_id);
    if (old && data.index !== 1 && data.index !== (old.delta_index ?? 0) + 1) throw new Error('sequence_gap');
    const unavailable = data.index !== 1 && (old?.content_unavailable === true || !old);
    const row: MessageRow = { ...old, id: data.message_id, type: 'ai', reply_id: data.reply_id as string | null,
      content: unavailable ? '' : (data.index === 1 ? '' : old?.content ?? '') + data.text,
      ...(unavailable ? { content_unavailable: true } : {}),
      state: 'draft', provisional: true, delta_index: Number(data.index), run_id: source.agent_run_id };
    agent.messages = old ? agent.messages!.map(item => item.id === row.id ? row : item) : [...agent.messages!, row];
  } else if (kind === 'message_completed' || kind === 'message_revised') {
    const message = JSON.parse(String(data.snapshot_json));
    if (typeof message.id !== 'string' || typeof message.type !== 'string') throw new Error('invalid_event');
    if (message.is_context_summary) return agent;
    const row: MessageRow = { ...message, content: textContent(message.content),
      state: 'complete', provisional: false, run_id: source.agent_run_id };
    agent.messages = agent.messages!.some(item => item.id === row.id)
      ? agent.messages!.map(item => item.id === row.id ? row : item) : [...agent.messages!, row];
    if (message.type === 'ai' && message.tool_calls?.length) {
      agent.batch_id = message.id;
      for (const call of message.tool_calls) {
        const old = agent.tool_states[call.id];
        const prior = old?.batch_id === message.id ? old : undefined;
        agent.tool_states[call.id] = { ...prior, tool_call_id: call.id, tool_name: call.name, args: call.args,
          batch_id: message.id, run_id: source.agent_run_id, state: prior?.state ?? 'pending' };
      }
    } else if (message.type === 'tool' && agent.tool_states[message.tool_call_id]) {
      const old = agent.tool_states[message.tool_call_id];
      agent.tool_states[message.tool_call_id] = { ...old, result: { ...message,
        content: textContent(message.content), state: 'complete', provisional: false } };
    }
  } else if (['tool_call_started', 'tool_call_finished', 'tool_progress'].includes(kind)) {
    const id = String(data.tool_call_id), old = agent.tool_states[id];
    if (kind === 'tool_progress') {
      if (old?.state === 'running' && old.run_id === source.agent_run_id && old.tool_name === data.tool_name &&
          (!old.batch_id || old.batch_id === data.batch_id))
        agent.tool_states[id] = { ...old, ...data, last_activity_at: event.timestamp } as ToolState;
    } else {
      const finishing = kind === 'tool_call_finished';
      agent.tool_states[id] = { ...old, ...data, run_id: source.agent_run_id,
        state: finishing ? 'finished' : 'running',
        ...(finishing ? { finished_at: event.timestamp } : { started_at: event.timestamp }) } as ToolState;
    }
  }
  return agent;
}
