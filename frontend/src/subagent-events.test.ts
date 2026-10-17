import { expect, it } from 'vitest';
import { applyEvent } from './events';
import type { RuntimeEvent, RunView } from './types';

it('routes child lifecycle and same call ids without ending root or mixing messages', () => {
  let view: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
  const event = (sequence: number, path: string, event_type: string, data: Record<string, unknown>): RuntimeEvent => ({
    version: 1, session_id: 's', run_id: 'r', sequence, event_type, data,
    source: { agent_path: path, agent_id: path.split('/').at(-1)!, agent_run_id: 'segment',
      parent_run_id: 'r', parent_tool_call_id: 'outer' },
  });
  view = applyEvent(view, event(1, 'root/tester/A', 'tool_call_started', {
    tool_call_id: 'same', tool_name: 'bash', boundary: 'dispatch' }));
  view = applyEvent(view, event(2, 'root/tester/B', 'tool_call_started', {
    tool_call_id: 'same', tool_name: 'read', boundary: 'dispatch' }));
  view = applyEvent(view, event(3, 'root/tester/A', 'message_delta', {
    message_id: 'child-text', text: 'child-only', index: 1 }));
  view = applyEvent(view, event(4, 'root/tester/A', 'run_finished', { status: 'completed' }));
  expect(view.status).toBe('running');
  expect(view.messages).toEqual([]);
  expect(view.agents?.['root/tester/A'].status).toBe('completed');
  expect(view.agents?.['root/tester/B'].tool_states.same.tool_name).toBe('read');
  expect(view.tool_states ?? {}).toEqual({});
  expect(view.last_sequence).toBe(4);
});

it('keeps model call ids that match object prototype names as ordinary rows', () => {
  const view: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
  const next = applyEvent(view, { version: 1, session_id: 's', run_id: 'r', sequence: 1,
    event_type: 'message_completed', source: { agent_path: 'root/tester/A', agent_id: 'A',
      agent_run_id: 'child', parent_run_id: 'r', parent_tool_call_id: 'outer' }, data: {
      snapshot_json: JSON.stringify({ id: 'batch', type: 'ai', content: '', tool_calls: [
        { id: '__proto__', name: 'read', args: { path: 'file' } } ] }) } });
  const rows = next.agents!['root/tester/A'].tool_states;
  expect(Object.hasOwn(rows, '__proto__')).toBe(true);
  expect(Object.keys(rows)).toEqual(['__proto__']);
  expect(rows.__proto__.tool_name).toBe('read');
});

it('a child stop receipt changes only that child and survives its control resume', () => {
  const source = { agent_path: 'root/tester/A', agent_id: 'A', agent_run_id: 'child', parent_run_id: 'r', parent_tool_call_id: 'outer' };
  let view: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
  view = applyEvent(view, { version: 1, session_id: 's', run_id: 'r', sequence: 1,
    event_type: 'control_accepted', data: { kind: 'stop' }, source });
  expect(view.agents?.[source.agent_path].status).toBe('stopping');
  expect(view.stop_requested).not.toBe(true);
  view = applyEvent(view, { version: 1, session_id: 's', run_id: 'r', sequence: 2,
    event_type: 'run_started', data: { mode: 'approval_resume' }, source: { ...source, agent_run_id: 'next' } });
  expect(view.agents?.[source.agent_path].status).toBe('stopping');
  expect(view.status).toBe('running');
});

it('keeps child text, tool results and earlier batches in its own message stream', () => {
  let view: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
  const source = { agent_path: 'root/tester/A', agent_id: 'A', agent_run_id: 'child', parent_run_id: 'r', parent_tool_call_id: 'outer' };
  let sequence = 0;
  const send = (event_type: string, data: Record<string, unknown>) => {
    view = applyEvent(view, { version: 1, session_id: 's', run_id: 'r', sequence: ++sequence, event_type, source, data });
  };
  const message = (row: object) => send('message_completed', { snapshot_json: JSON.stringify(row) });
  send('message_delta', { message_id: 'one', text: 'draft', index: 1 });
  expect(view.agents?.[source.agent_path].messages?.[0].content).toBe('draft');
  message({ id: 'one', type: 'ai', content: 'first', tool_calls: [{ id: 'x', name: 'read', args: { path: 'a' } }] });
  message({ id: 'result', type: 'tool', content: 'file', tool_call_id: 'x', status: 'success' });
  message({ id: 'two', type: 'ai', content: 'second', tool_calls: [{ id: 'y', name: 'grep', args: {} }] });
  message({ id: 'final', type: 'ai', content: 'report' });
  expect(view.agents?.[source.agent_path].messages?.map(row => row.content)).toEqual(['first', 'file', 'second', 'report']);
  expect(view.agents?.[source.agent_path].tool_states.x.result?.content).toBe('file');
  expect(view.messages).toEqual([]);
  expect(view.status).toBe('running');
});

it('records the original parent batch before later calls reuse the outer id', () => {
  let view: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [
    { id: 'one', type: 'ai', content: '', state: 'complete', provisional: false,
      tool_calls: [{ id: 'outer', name: 'subagent', args: {} }] } ],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
  const event = (sequence: number, id: string): RuntimeEvent => ({ version: 1, session_id: 's', run_id: 'r', sequence,
    event_type: 'run_started', data: {}, source: { agent_path: `root/tester/${id}`, agent_id: id,
      agent_run_id: id, parent_run_id: 'r', parent_tool_call_id: 'outer' } });
  view = applyEvent(view, event(1, 'A'));
  view = { ...view, messages: [...view.messages, { ...view.messages[0], id: 'two' }] };
  view = applyEvent(view, event(2, 'B'));
  view = applyEvent(view, { ...event(3, 'A'), event_type: 'approval_requested' });
  expect(view.agents!['root/tester/A'].parent_batch_id).toBe('one');
  expect(view.agents!['root/tester/B'].parent_batch_id).toBe('two');
});

it('continues a draft from a summary snapshot without breaking the root stream', () => {
  const source = { agent_path: 'root/tester/A', agent_id: 'A', agent_run_id: 'child', parent_run_id: 'r', parent_tool_call_id: 'outer' };
  let view: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 4, incomplete: false, done: false, manual_review_required: false,
    agents: { [source.agent_path]: { source, status: 'running', tool_states: {}, messages: [
      { id: 'draft', type: 'ai', content: 'first', state: 'draft', provisional: true, delta_index: 1 } ] } } };
  view = applyEvent(view, { version: 1, session_id: 's', run_id: 'r', sequence: 5,
    event_type: 'message_delta', source, data: { message_id: 'draft', index: 2, text: ' second' } });
  expect(view.agents![source.agent_path].messages![0].content).toBe('first second');
  // A bounded summary may omit an oversized prefix. This must not end root SSE.
  view = { ...view, agents: { [source.agent_path]: { source, status: 'running', tool_states: {} } } };
  expect(() => { view = applyEvent(view, { version: 1, session_id: 's', run_id: 'r', sequence: 6,
    event_type: 'message_delta', source, data: { message_id: 'huge', index: 20, text: 'tail' } }); }).not.toThrow();
  expect(view.agents![source.agent_path].messages![0].content_unavailable).toBe(true);
  view = applyEvent(view, { version: 1, session_id: 's', run_id: 'r', sequence: 7,
    event_type: 'approval_requested', source, data: {} });
  expect(view.agents![source.agent_path].status).toBe('awaiting_approval');
});
