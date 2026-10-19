import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { MessageRow } from '../types';
import Conversation from './Conversation';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const row = (overrides: Partial<MessageRow> = {}): MessageRow => ({
  id: 'long-answer', reply_id: 'reply', type: 'ai', content: '```python\nprint("preview")', provisional: false, state: 'complete',
  content_truncated: true, detail: { sequence: 2, revision: 1 }, sequence: 2, revision: 1, ...overrides,
});
// These tests exercise manual loading of older messages. Latest-body loading has its own suite.
const older = (...messages: MessageRow[]) => [...messages, row({ id: 'newer', content: '',
  content_truncated: false, detail: undefined, sequence: undefined })];

it('loads saved code to its end and copies the complete answer and code', async () => {
  const user = userEvent.setup();
  const code = 'print("preview")\n' + 'print("中文😀")\n'.repeat(1400) + 'print("END_OF_SAVED_CODE")\n';
  const full = '```python\n' + code + '```';
  const load = vi.fn().mockResolvedValue(full);
  const source = row();
  const { container } = render(<Conversation compacted={false} messages={older(source)} loadContent={load} />);
  expect(load).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '复制回答' }).textContent).toBe('复制已加载内容');
  await user.click(screen.getByRole('button', { name: '加载完整内容' }));
  await waitFor(() => expect(container.querySelector('pre code')?.textContent).toBe(code));
  expect(load.mock.calls[0][0]).toEqual(source);
  expect(load.mock.calls[0][1]).toBeInstanceOf(AbortSignal);
  expect(screen.queryByText('当前为消息预览，完整内容仍保存在会话中。')).toBeNull();
  await user.click(screen.getByRole('button', { name: '复制代码' }));
  expect(await navigator.clipboard.readText()).toBe(code);
  await user.click(screen.getByRole('button', { name: '复制回答' }));
  expect(await navigator.clipboard.readText()).toBe(full);
  expect(source.content_truncated).toBe(true);
});

it('retains preview on a failed load, avoids duplicate requests, and retries', async () => {
  let reject!: (error: Error) => void;
  const load = vi.fn().mockImplementationOnce(() => new Promise<string>((_, no) => { reject = no; }))
    .mockResolvedValueOnce('complete answer');
  render(<Conversation compacted={false} messages={older(row({ content: 'saved preview' }))} loadContent={load} />);
  fireEvent.click(screen.getByRole('button', { name: '加载完整内容' }));
  expect(screen.getByRole('button', { name: '正在加载完整内容…' })).toHaveProperty('disabled', true);
  await act(async () => reject(new Error('network')));
  expect(screen.getByText('saved preview')).toBeTruthy();
  expect(screen.getByRole('alert').textContent).toContain('重试');
  fireEvent.click(screen.getByRole('button', { name: '加载完整内容' }));
  expect(await screen.findByText('complete answer')).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(2);
});

it('discards and cancels a delayed result when the message revision changes', async () => {
  let finish!: (text: string) => void;
  const load = vi.fn().mockImplementation(() => new Promise<string>(resolve => { finish = resolve; }));
  const { rerender } = render(<Conversation compacted={false} messages={older(row({ content: 'old preview' }))} loadContent={load} />);
  fireEvent.click(screen.getByRole('button', { name: '加载完整内容' }));
  const signal = load.mock.calls[0][1] as AbortSignal;
  rerender(<Conversation compacted={false} messages={older(row({ content: 'new preview', revision: 2,
    detail: { sequence: 2, revision: 2 } }))} loadContent={load} />);
  expect(signal.aborted).toBe(true);
  await act(async () => finish('STALE_BODY'));
  expect(screen.queryByText('STALE_BODY')).toBeNull();
  expect(screen.getByText('new preview')).toBeTruthy();
});

it('invalidates pending and loaded content when the conversation scope changes', async () => {
  let finish!: (text: string) => void;
  const load = vi.fn().mockResolvedValueOnce('old complete')
    .mockImplementationOnce(() => new Promise<string>(resolve => { finish = resolve; }));
  const first = row({ content: 'old preview' });
  const second = row({ id: 'second', content: 'second preview', sequence: 3, detail: { sequence: 3, revision: 1 } });
  const { rerender } = render(<Conversation compacted={false} messages={older(first, second)} contentScope="session:0" loadContent={load} />);
  fireEvent.click(screen.getAllByRole('button', { name: '加载完整内容' })[0]);
  expect(await screen.findByText('old complete')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '加载完整内容' }));
  const signal = load.mock.calls[1][1] as AbortSignal;
  rerender(<Conversation compacted={false} messages={older(first, second)} contentScope="session:1" loadContent={load} />);
  expect(signal.aborted).toBe(true);
  expect(screen.queryByText('old complete')).toBeNull();
  await act(async () => finish('STALE_SECOND'));
  expect(screen.queryByText('STALE_SECOND')).toBeNull();
});

it('loads the original content of a tool result through the same action', async () => {
  const load = vi.fn().mockResolvedValue('complete tool result END');
  render(<Conversation compacted={false} loadContent={load} messages={[
    row({ id: 'call', content: '', content_truncated: false, tool_calls: [{ id: 'tool-call', name: 'read', args: { path: 'code.py' } }] }),
    row({ id: 'tool-result', type: 'tool', content: 'tool preview', tool_call_id: 'tool-call', name: 'read',
      status: 'success', parent_message_id: 'call', sequence: 3, detail: { sequence: 3, revision: 1 } }),
  ]} />);
  fireEvent.click(screen.getByText('read', { exact: true }));
  fireEvent.click(screen.getByRole('button', { name: '加载完整内容' }));
  expect(await screen.findByText('complete tool result END')).toBeTruthy();
  expect(load.mock.calls[0][0].id).toBe('tool-result');
});

it('does not load an incomplete draft or a preview without a saved identity', () => {
  const load = vi.fn();
  render(<Conversation compacted={false} loadContent={load} messages={[
    row({ id: 'draft', content: 'draft preview', provisional: true, state: 'draft', content_unavailable: true }),
    row({ id: 'no-detail', content: 'unavailable preview', detail: undefined }),
  ]} />);
  expect(screen.queryByRole('button', { name: '加载完整内容' })).toBeNull();
  expect(screen.getByText('较长的生成中内容暂未完整显示，完成后同步。')).toBeTruthy();
  expect(load).not.toHaveBeenCalled();
});

it('also makes a truncated context summary readable', async () => {
  const load = vi.fn().mockResolvedValue('complete saved summary');
  render(<Conversation compacted={true} loadContent={load} messages={[row({ is_context_summary: true, content: 'summary preview' })]} />);
  fireEvent.click(screen.getByRole('button', { name: '加载完整内容' }));
  expect(await screen.findByText('complete saved summary')).toBeTruthy();
});
