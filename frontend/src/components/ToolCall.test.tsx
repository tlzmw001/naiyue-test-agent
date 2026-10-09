import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import Conversation from './Conversation';

afterEach(cleanup);
it('renders a correlated tool result once, escapes content, and separates it from assistant speech', () => {
  const { container } = render(<Conversation compacted={false} messages={[
    { id: 'ai', type: 'ai', content: '', state: 'complete', provisional: false,
      tool_calls: [{ id: 't', name: 'read', args: { path: 'a.txt' } }] },
    { id: 'result', type: 'tool', content: '<script>tool body</script>', tool_call_id: 't', status: 'error',
      state: 'complete', provisional: false },
  ] as any} />);
  expect(screen.getByText('read')).toBeTruthy();
  expect(screen.getByText('错误结果')).toBeTruthy();
  expect(screen.getAllByText('<script>tool body</script>')).toHaveLength(1);
  expect(container.querySelector('script')).toBeNull();
});

it('does not attach a later tool result to an earlier turn when a call ID is reused', () => {
  render(<Conversation compacted={false} messages={[
    { id: 'ai-one', type: 'ai', content: '', state: 'complete', provisional: false,
      tool_calls: [{ id: 'reused', name: 'read', args: { path: 'first.txt' } }] },
    { id: 'one', type: 'tool', content: 'first result', tool_call_id: 'reused', status: 'success', state: 'complete', provisional: false },
    { id: 'ai-two', type: 'ai', content: '', state: 'complete', provisional: false,
      tool_calls: [{ id: 'reused', name: 'read', args: { path: 'second.txt' } }] },
    { id: 'two', type: 'tool', content: 'second result', tool_call_id: 'reused', status: 'error', state: 'complete', provisional: false },
  ] as any} />);
  expect(screen.getAllByText('first result')).toHaveLength(1);
  expect(screen.getAllByText('second result')).toHaveLength(1);
});
