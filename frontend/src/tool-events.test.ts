import { expect, it } from 'vitest';
import { applyEvent, validateEvent } from './events';

function event(sequence: number, event_type: string, data: object) {
  return validateEvent({ version: 1, session_id: 's', run_id: 'r', sequence, event_type, data });
}
it('preserves tool call arguments and result correlation through complete snapshots', () => {
  let view = { messages: [], last_sequence: 0, status: 'running' } as any;
  view = applyEvent(view, event(1, 'message_completed', { snapshot_json: JSON.stringify({
    id: 'ai', type: 'ai', content: '', tool_calls: [{ id: 't', name: 'read', args: { path: 'a.txt' } }],
  }) }));
  view = applyEvent(view, event(2, 'message_completed', { snapshot_json: JSON.stringify({
    id: 'result', type: 'tool', content: 'missing file', tool_call_id: 't', status: 'error', name: 'read',
  }) }));
  expect(view.messages[0].tool_calls[0].args).toEqual({ path: 'a.txt' });
  expect(view.messages[1]).toMatchObject({ tool_call_id: 't', status: 'error', name: 'read' });
});
it('tracks dispatch outcomes and leaves unfinished dispatch unconfirmed at terminal', () => {
  let view = { messages: [], last_sequence: 0, status: 'running' } as any;
  view = applyEvent(view, event(1, 'tool_call_started', { tool_call_id: 't', tool_name: 'read', boundary: 'dispatch' }));
  expect(view.tool_states.t.state).toBe('running');
  view = applyEvent(view, event(2, 'tool_call_finished', { tool_call_id: 't', tool_name: 'read', boundary: 'dispatch', outcome: 'error_result' }));
  expect(view.tool_states.t.outcome).toBe('error_result');
  view = applyEvent(view, event(3, 'tool_call_started', { tool_call_id: 'unknown', tool_name: 'write', boundary: 'dispatch' }));
  view = applyEvent(view, event(4, 'run_finished', { status: 'failed' }));
  expect(view.tool_states.unknown.state).toBe('unconfirmed');
});
