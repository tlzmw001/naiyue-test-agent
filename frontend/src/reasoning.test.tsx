import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import Conversation from './components/Conversation';
import { applyEvent } from './events';

afterEach(cleanup);
it('reasoning is separate, collapsed by default, and absent without real text', () => {
  const { container } = render(<Conversation compacted={false} messages={[{
    id: 'a', type: 'ai', content: 'answer', reasoning: 'actual reasoning', provisional: false, state: 'complete',
  }]} />);
  expect(container.querySelector('details')?.open).toBe(false);
  fireEvent.click(screen.getByText('思考内容'));
  expect(screen.getByText('actual reasoning')).toBeTruthy();
  expect(screen.getByText('answer')).toBeTruthy();
});

it('reasoning and text deltas preserve each other and final snapshots calibrate', () => {
  let view: any = { run_id: 'r', session_id: 's', last_sequence: 0, messages: [], status: 'running' };
  const event = (event_type: string, sequence: number, data: any) => ({ version: 1, run_id: 'r', session_id: 's', sequence, event_type, data } as const);
  view = applyEvent(view, event('message_reasoning_delta', 1, { message_id: 'a', text: 'reason', index: 1 }));
  view = applyEvent(view, event('message_delta', 2, { message_id: 'a', text: 'answer', index: 1 }));
  expect(view.messages[0].reasoning).toBe('reason');
  expect(view.messages[0].content).toBe('answer');
  view = applyEvent(view, event('message_completed', 3, { snapshot_json: JSON.stringify({ id: 'a', type: 'ai', content: 'final', reasoning: 'final reason' }) }));
  expect(view.messages[0].reasoning).toBe('final reason');
});
