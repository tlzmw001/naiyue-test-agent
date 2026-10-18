import type { MessageRow } from './types';

export function reasoningDelta(messages: MessageRow[], data: Record<string, unknown>, runId: string): MessageRow[] {
  if (typeof data.message_id !== 'string' || typeof data.text !== 'string' || !Number.isInteger(data.index) || Number(data.index) < 1)
    throw new Error('invalid_event');
  const old = messages.find(row => row.id === data.message_id);
  if (data.index !== 1 && data.index !== (old?.reasoning_index ?? 0) + 1) throw new Error('sequence_gap');
  const row: MessageRow = { ...old, id: data.message_id, type: 'ai', content: old?.content ?? '',
    reasoning: (data.index === 1 ? '' : old?.reasoning ?? '') + data.text, reasoning_index: Number(data.index),
    reply_id: data.reply_id as string | null, run_id: runId, provisional: true, state: 'draft' };
  return old ? messages.map(item => item.id === row.id ? row : item) : [...messages, row];
}
