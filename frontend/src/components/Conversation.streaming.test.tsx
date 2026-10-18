import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import { applyEvent, validateEvent } from '../events';
import type { MessageRow, RunView } from '../types';
import Conversation from './Conversation';

afterEach(cleanup);
const initial: RunView = { session_id: 's', run_id: 'r', messages: [], last_sequence: 0,
  status: 'running', done: false, incomplete: false, manual_review_required: false };
const frame = (sequence: number, event_type: string, data: Record<string, unknown>) =>
  validateEvent({ version: 1, session_id: 's', run_id: 'r', sequence, event_type, data });

it('renders accumulated events before completion and keeps the same message and table through final calibration', async () => {
  const user = userEvent.setup();
  const parts = ['**正在检查', '**\n\n| 检查项 |\n| --- |\n| 配置 |\n\n- [x] 已读\n\n```python\nprint("测试")',
    '\n```\n\n[检查结果](https://example.com/result)'];
  let view = applyEvent(initial, frame(1, 'message_delta', { message_id: 'm', index: 1, text: parts[0] }));
  const { container, rerender } = render(<Conversation compacted={false} messages={view.messages} />);
  const article = container.querySelector('article');
  expect(screen.getByText('正在生成')).toBeTruthy();
  expect(container.querySelector('strong')).toBeNull();
  view = applyEvent(view, frame(2, 'message_delta', { message_id: 'm', index: 2, text: parts[1] }));
  rerender(<Conversation compacted={false} messages={view.messages} />);
  expect(container.querySelector('strong')?.textContent).toBe('正在检查');
  const table = screen.getByRole('table');
  const code = container.querySelector('pre code');
  expect(code?.textContent).toBe('print("测试")\n');
  expect(screen.getByRole<HTMLInputElement>('checkbox').disabled).toBe(true);
  expect(view.messages[0].content).toBe(parts.slice(0, 2).join(''));
  await user.click(screen.getByRole('button', { name: '复制回答' }));
  expect(await navigator.clipboard.readText()).toBe(parts.slice(0, 2).join(''));
  view = applyEvent(view, frame(3, 'message_delta', { message_id: 'm', index: 3, text: parts[2] }));
  rerender(<Conversation compacted={false} messages={view.messages} />);
  expect(screen.getByRole('link', { name: '检查结果' }).getAttribute('href')).toBe('https://example.com/result');
  expect(screen.getByText('正在生成')).toBeTruthy();
  const finalContent = parts.join('') + '\n\n**最终校准**';
  view = applyEvent(view, frame(4, 'message_completed', {
    snapshot_json: JSON.stringify({ id: 'm', type: 'ai', content: finalContent }),
  }));
  rerender(<Conversation compacted={false} messages={view.messages} />);
  expect(screen.queryByText('正在生成')).toBeNull();
  expect(screen.getByText('最终校准').tagName).toBe('STRONG');
  expect(container.querySelectorAll('article')).toHaveLength(1);
  expect(container.querySelector('article')).toBe(article);
  expect(screen.getByRole('table')).toBe(table);
  expect(container.querySelector('pre code')).toBe(code);
  expect(view.messages[0]).toMatchObject({ content: finalContent, provisional: false, state: 'complete' });
});

it.each([
  ['**检查', '**', 'strong'],
  ['[文档](https://example.com/', 'result)', 'a[href="https://example.com/result"]'],
  ['| 检查项 |', '\n| --- |\n| 已读 |', 'table'],
])('recognizes incomplete syntax only as the missing text arrives: %s', (prefix, suffix, selector) => {
  const row: MessageRow = { id: 'm', type: 'ai', content: prefix, provisional: true, state: 'draft' };
  const { container, rerender } = render(<Conversation compacted={false} messages={[row]} />);
  // GFM can autolink the partial URL; the named Markdown destination is not complete yet.
  expect(container.querySelector(selector)).toBeNull();
  rerender(<Conversation compacted={false} messages={[{ ...row, content: prefix + suffix }]} />);
  expect(container.querySelector(selector)).not.toBeNull();
  expect(row.content).toBe(prefix);
  expect(screen.getByText('正在生成')).toBeTruthy();
});

it.each(['stopped', 'failed'])('keeps parsed partial content and its unconfirmed marker after %s', status => {
  const content = '**已收到**\n\n```python\npartial';
  let view = applyEvent(initial, frame(1, 'message_delta', { message_id: 'm', index: 1, text: content }));
  const { container, rerender } = render(<Conversation compacted={false} messages={view.messages} />);
  expect(container.querySelector('strong')?.textContent).toBe('已收到');
  const code = container.querySelector('pre code');
  view = applyEvent(view, frame(2, 'run_finished', { status, manual_review_required: false }));
  rerender(<Conversation compacted={false} messages={view.messages} />);
  expect(screen.queryByText('正在生成')).toBeNull();
  expect(screen.getByText('未完成内容')).toBeTruthy();
  expect(container.querySelector('strong')?.textContent).toBe('已收到');
  expect(container.querySelector('pre code')).toBe(code);
  expect(code?.textContent).toBe('partial\n');
  expect(view.messages[0]).toMatchObject({ content, provisional: true, state: 'unconfirmed' });
});

it('applies HTML, link and image restrictions to intermediate fragments as well as final-looking drafts', () => {
  const fragments = ['**普通正文**\n\n<script>alert(1)',
    '</script>\n\n[危险](javascript:alert%281%29) ![远程](https://example.com/private.png)\n\n<img src=x onerror="alert(1)">'];
  const { container, rerender } = render(<Conversation compacted={false} messages={[]} />);
  for (let index = 0; index < fragments.length; index++) {
    rerender(<Conversation compacted={false} messages={[{ id: 'unsafe', type: 'ai',
      content: fragments.slice(0, index + 1).join(''), provisional: true, state: 'draft' }]} />);
    expect(container.querySelector('strong')?.textContent).toBe('普通正文');
    expect(container.querySelector('script, img, [onerror]')).toBeNull();
    expect([...container.querySelectorAll('a')].some(link => /^javascript:/i.test(link.getAttribute('href') ?? ''))).toBe(false);
  }
  expect(screen.getByText('[图片：远程]')).toBeTruthy();
});

it('replaces a switched session view without keeping the previous parsed draft or unfinished marker', () => {
  const row: MessageRow = { id: 'm', type: 'ai', content: '**旧会话**', provisional: true, state: 'unconfirmed' };
  const { container, rerender } = render(<Conversation key="old" compacted={false} messages={[row]} />);
  expect(container.querySelector('strong')?.textContent).toBe('旧会话');
  rerender(<Conversation key="new" compacted={false} messages={[{ ...row, content: '**新会话**', state: 'draft' }]} />);
  expect(screen.queryByText('旧会话')).toBeNull();
  expect(screen.queryByText('未完成内容')).toBeNull();
  expect(screen.getByText('正在生成')).toBeTruthy();
  expect(container.querySelector('strong')?.textContent).toBe('新会话');
});

it('preserves an 8 KiB source and parsed structure over 32 consecutive updates', () => {
  const chunk = 'x'.repeat(256);
  let view = initial;
  const { container, rerender } = render(<Conversation compacted={false} messages={[]} />);
  for (let index = 1; index <= 32; index++) {
    view = applyEvent(view, frame(index, 'message_delta', {
      message_id: 'large', index, text: index === 1 ? '**连续更新**\n\n' + chunk : chunk,
    }));
    rerender(<Conversation compacted={false} messages={view.messages} />);
    expect(container.querySelector('strong')?.textContent).toBe('连续更新');
    expect(view.messages[0].content).toBe('**连续更新**\n\n' + chunk.repeat(index));
  }
  expect(container.querySelectorAll('article')).toHaveLength(1);
  expect(screen.getByText(chunk.repeat(32))).toBeTruthy();
  expect(view.messages[0].provisional).toBe(true);
});
