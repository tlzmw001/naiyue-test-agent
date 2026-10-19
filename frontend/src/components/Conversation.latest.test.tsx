import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ReactMarkdown from 'react-markdown';
import { mergeMessages } from '../events';
import type { MessageRow } from '../types';
import Conversation from './Conversation';

vi.mock('react-markdown', async original => {
  const actual = await original<typeof import('react-markdown')>();
  return { ...actual, default: vi.fn(actual.default) };
});
beforeEach(() => vi.clearAllMocks());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const preview = (overrides: Partial<MessageRow> = {}): MessageRow => ({
  id: 'latest', type: 'ai', content: '```text\nstart', content_truncated: true,
  provisional: false, state: 'complete', sequence: 2, revision: 1, detail: { sequence: 2, revision: 1 }, ...overrides,
});

it('automatically loads only the latest saved assistant body, without per-chunk renders', async () => {
  let finish!: (value: string) => void;
  const load = vi.fn().mockImplementation(() => new Promise<string>(resolve => { finish = resolve; }));
  const latest = preview();
  const code = 'start\n' + '中文😀 retained code\n'.repeat(2500) + 'END_OF_LATEST\n';
  const full = '```text\n' + code + '```';
  const { container } = render(<Conversation compacted={false} loadContent={load} messages={[
    preview({ id: 'old', sequence: 1, content: 'old preview', detail: { sequence: 1, revision: 1 } }),
    latest,
    preview({ id: 'tool', type: 'tool', content: 'tool preview', tool_call_id: 't' }),
  ]} />);
  expect(load).toHaveBeenCalledTimes(1);
  expect(load.mock.calls[0][0].id).toBe('latest');
  const parses = vi.mocked(ReactMarkdown).mock.calls.length;
  await act(async () => finish(full));
  await waitFor(() => expect(container.querySelector('pre code')?.textContent).toBe(code));
  expect(vi.mocked(ReactMarkdown).mock.calls.length - parses).toBe(1);
  expect(screen.getByText('old preview')).toBeTruthy();
  expect(screen.getAllByRole('button', { name: '加载完整内容' })).toHaveLength(2);
});

it('keeps streamed text and its DOM through an archive preview, then corrects once', async () => {
  let finish!: (value: string) => void;
  const load = vi.fn().mockImplementation(() => new Promise<string>(resolve => { finish = resolve; }));
  const streamed = '```text\nstart\nreceived streamed tail\n';
  const draft = preview({ content: streamed, provisional: true, state: 'draft', content_truncated: false,
    sequence: undefined, revision: undefined, detail: undefined });
  const { container, rerender } = render(<Conversation compacted={false} loadContent={load} messages={[draft]} />);
  const code = container.querySelector('pre code');
  const scroll = screen.getByRole('region', { name: '代码，可横向滚动' });
  scroll.focus(); scroll.scrollLeft = 120;
  expect(load).not.toHaveBeenCalled();
  const rows = mergeMessages([draft], [preview()]);
  rerender(<Conversation compacted={false} loadContent={load} messages={rows} />);
  expect(rows[0].content).toBe(streamed);
  expect(rows[0].content_truncated).toBe(true);
  expect(container.querySelector('pre code')).toBe(code);
  expect(code?.textContent).toContain('received streamed tail');
  expect(scroll.scrollLeft).toBe(120);
  expect(document.activeElement).toBe(scroll);
  await act(async () => finish(streamed + 'saved final tail\n```'));
  expect(code?.textContent).toContain('saved final tail');
  expect(container.querySelector('pre code')).toBe(code);
  expect(scroll.scrollLeft).toBe(120);
});

it('does not refetch or reparse an unchanged complete message during unrelated updates', () => {
  const load = vi.fn();
  const full = preview({ content: '```python\nprint("complete")\n```', content_truncated: false });
  const { container, rerender } = render(<Conversation compacted={false} loadContent={load} messages={[full]} />);
  const code = container.querySelector('pre code');
  const parses = vi.mocked(ReactMarkdown).mock.calls.length;
  const merged = mergeMessages([full], [preview({ content: '```python\nprint' })]);
  rerender(<Conversation compacted={false} loadContent={load} messages={merged} toolStates={{}} />);
  expect(container.querySelector('pre code')).toBe(code);
  expect(code?.textContent).toBe('print("complete")\n');
  expect(vi.mocked(ReactMarkdown)).toHaveBeenCalledTimes(parses);
  expect(load).not.toHaveBeenCalled();
});

it('keeps a failed automatic load readable and retries only on user action', async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce('retried full');
  const source = preview({ content: 'retained preview' });
  const { rerender } = render(<Conversation compacted={false} loadContent={load} messages={[source]} />);
  expect((await screen.findByRole('alert')).textContent).toContain('重试');
  rerender(<Conversation compacted={false} loadContent={load} messages={[{ ...source }]} />);
  expect(load).toHaveBeenCalledTimes(1);
  expect(screen.getByText('retained preview')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '加载完整内容' }));
  expect(await screen.findByText('retried full')).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(2);
});

it('cancels automatic loads and ignores late content from a replaced scope', async () => {
  let finish!: (value: string) => void;
  const load = vi.fn().mockImplementationOnce(() => new Promise<string>(resolve => { finish = resolve; }))
    .mockResolvedValueOnce('new scope complete');
  const { rerender } = render(<Conversation compacted={false} contentScope="old:0" loadContent={load} messages={[preview()]} />);
  const signal = load.mock.calls[0][1] as AbortSignal;
  rerender(<Conversation compacted={false} contentScope="new:0" loadContent={load} messages={[preview()]} />);
  expect(signal.aborted).toBe(true);
  expect(await screen.findByText('new scope complete')).toBeTruthy();
  await act(async () => finish('STALE_SCOPE'));
  expect(screen.queryByText('STALE_SCOPE')).toBeNull();
});

it.each(['mismatch', 'revision'] as const)('does not reuse a streamed body after %s', reason => {
  const draft = preview({ content: 'original stream', provisional: true, content_truncated: false });
  const next = preview({ content: reason === 'mismatch' ? 'different' : 'original',
    revision: reason === 'revision' ? 2 : 1 });
  expect(mergeMessages([draft], [next])[0].content).toBe(next.content);
});

it('automatically completes a body over one MiB after resync confirms the old draft', async () => {
  const code = 'start\n' + 'x'.repeat(1024 * 1024 + 4096) + '\nEND_AFTER_RESYNC\n';
  const full = '```text\n' + code + '```';
  const load = vi.fn().mockResolvedValue(full);
  const draft = preview({ provisional: true, state: 'unconfirmed', content_unavailable: true });
  const { container, rerender } = render(<Conversation compacted={false} loadContent={load} messages={[draft]} />);
  expect(load).not.toHaveBeenCalled();
  const rows = mergeMessages([draft], [preview()]);
  rerender(<Conversation compacted={false} loadContent={load} messages={rows} />);
  await waitFor(() => expect(container.querySelector('pre code')?.textContent).toBe(code));
  expect(load).toHaveBeenCalledTimes(1);
  expect(load.mock.calls[0][0].provisional).toBe(false);
  expect(screen.queryByRole('button', { name: '加载完整内容' })).toBeNull();
  expect(screen.queryByText('未完成内容')).toBeNull();
});
