import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import Conversation from './Conversation';
import type { MessageRow } from '../types';

afterEach(cleanup);
it('one task has one divider and one copy action across tool rounds', () => {
  const rows: MessageRow[] = [
    { id: 'h', type: 'human', content: 'task', reply_id: 'r', provisional: false, state: 'complete' },
    { id: 'a', type: 'ai', content: 'looking', reply_id: 'r', provisional: false, state: 'complete' },
    { id: 'b', type: 'ai', content: 'answer', reply_id: 'r', provisional: false, state: 'complete' },
  ];
  const { container } = render(<Conversation messages={rows} compacted={false} />);
  expect(container.querySelectorAll('hr')).toHaveLength(1);
  expect(screen.getAllByRole('button', { name: '复制回答' })).toHaveLength(1);
});

it('offers regeneration only at the current last task and respects the run gate', () => {
  render(<Conversation compacted={false} lastReplyId="last" canRegenerate={false} regenerate={() => {}}
    messages={['old', 'last'].map(id => ({ id, reply_id: id, type: 'ai', content: id, provisional: false, state: 'complete' }))} />);
  const buttons = screen.getAllByRole('button', { name: '重新生成' });
  expect(buttons).toHaveLength(1);
  expect(buttons[0].hasAttribute('disabled')).toBe(true);
});
