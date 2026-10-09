import { expect, it } from 'vitest';
import { applyEvent, validateEvent } from './events';

const event = (sequence: number, event_type: string, data: object) => ({
  version: 1, sequence, event_type, session_id: 's', run_id: 'r', data,
});

it('calibrates a draft with a complete snapshot and ignores duplicates', () => {
  let view = { messages: [], last_sequence: 0, status: 'running' } as any;
  view = applyEvent(view, validateEvent(event(1, 'run_started', {})));
  view = applyEvent(view, validateEvent(event(2, 'message_delta', { message_id: 'm', text: 'part', index: 1 })));
  expect(view.messages[0].provisional).toBe(true);
  const complete = validateEvent(event(3, 'message_completed', {
    snapshot_json: JSON.stringify({ id: 'm', type: 'ai', content: 'complete', is_context_summary: false }),
  }));
  view = applyEvent(view, complete);
  view = applyEvent(view, complete);
  expect(view.messages).toHaveLength(1);
  expect(view.messages[0]).toMatchObject({ content: 'complete', provisional: false });
});

it('marks stopped drafts unconfirmed and rejects sequence gaps', () => {
  const view = { messages: [{ id: 'm', type: 'ai', content: 'partial', provisional: true }], last_sequence: 4,
                 status: 'running' } as any;
  const stopped = applyEvent(view, validateEvent(event(5, 'run_finished', { status: 'stopped' })));
  expect(stopped.messages[0].state).toBe('unconfirmed');
  expect(() => applyEvent(view, validateEvent(event(6, 'run_finished', { status: 'completed' })))).toThrow('sequence_gap');
});

it('validates network event envelopes at runtime', () => {
  expect(() => validateEvent({ version: 1, sequence: '1' })).toThrow('invalid_event');
  expect(() => validateEvent(event(1, 'run_finished', { status: 'stoped' }))).toThrow('invalid_event');
});
