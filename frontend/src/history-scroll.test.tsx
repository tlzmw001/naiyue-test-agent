import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Conversation from './components/Conversation';
import type { HistoryControls, MessageRow } from './types';

afterEach(cleanup);
const row = (n: number): MessageRow => ({ id: `m${n}`, type: 'ai', content: `message ${n}`, state: 'complete', provisional: false });
const control = (loadOlder = vi.fn().mockResolvedValue(undefined)): HistoryControls => ({
  enabled: true, hasMore: true, loading: false, error: '', loadOlder,
});

it('loads only after a top scroll and restores the same visible message offset', async () => {
  let messages = [row(3), row(4)];
  const history = control();
  const { container, rerender } = render(<Conversation messages={messages} compacted={false} history={history} />);
  const scroll = container.firstElementChild as HTMLDivElement;
  Object.defineProperties(scroll, { clientHeight: { get: () => 100 },
    scrollHeight: { get: () => messages.length * 500 } });
  scroll.getBoundingClientRect = () => ({ top: 0, bottom: 100 } as DOMRect);
  const rectangles = () => container.querySelectorAll<HTMLElement>('article').forEach((node, index) => {
    node.getBoundingClientRect = () => ({ top: index * 500 - scroll.scrollTop,
      bottom: (index + 1) * 500 - scroll.scrollTop } as DOMRect);
  });
  rectangles();
  expect(history.loadOlder).not.toHaveBeenCalled();
  scroll.scrollTop = 60;
  fireEvent.scroll(scroll);
  expect(history.loadOlder).toHaveBeenCalledTimes(1);
  const originalOffset = container.querySelector<HTMLElement>('[data-message-id="m3"]')!.getBoundingClientRect().top;
  messages = [row(1), row(2), ...messages];
  // Existing DOM rows use their current index, including during layout effects.
  container.querySelectorAll<HTMLElement>('article').forEach(node => {
    node.getBoundingClientRect = () => {
      const index = messages.findIndex(item => item.id === node.dataset.messageId);
      return { top: index * 500 - scroll.scrollTop, bottom: (index + 1) * 500 - scroll.scrollTop } as DOMRect;
    };
  });
  rerender(<Conversation messages={messages} compacted={false} history={history} />);
  expect(scroll.scrollTop).toBe(1060);
  expect(container.querySelector<HTMLElement>('[data-message-id="m3"]')!.getBoundingClientRect().top).toBe(originalOffset);
  messages = [...messages, row(5)];
  rerender(<Conversation messages={messages} compacted={false} history={history} />);
  expect(scroll.scrollTop).toBe(1060);
  expect(screen.getByRole('button', { name: '回到最新 ↓' })).toBeTruthy();
});

it('does not loop after an error and exposes retry and the beginning', async () => {
  const history = { ...control(), error: '读取未确认' };
  const { container, rerender } = render(<Conversation messages={[row(3)]} compacted={false} history={history} />);
  fireEvent.scroll(container.firstElementChild!);
  expect(history.loadOlder).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '重试加载历史' })));
  expect(history.loadOlder).toHaveBeenCalledTimes(1);
  rerender(<Conversation messages={[row(3)]} compacted={false} history={{ ...history, hasMore: false, error: '' }} />);
  expect(screen.getByText('已到达历史起点')).toBeTruthy();
});

it('marks truncated message and tool argument previews explicitly', () => {
  render(<Conversation messages={[{ ...row(1), content_truncated: true, arguments_truncated: true, detail: { sequence: 1, revision: 1 } }]}
    compacted={false} />);
  expect(screen.getByText('当前为消息预览，完整内容仍保存在会话中。')).toBeTruthy();
  expect(screen.getByText('工具调用信息未完整显示。')).toBeTruthy();
});

it('does not claim that a truncated live draft has already been saved', () => {
  render(<Conversation messages={[{ ...row(1), provisional: true, state: 'draft', content_unavailable: true }]}
    compacted={false} />);
  expect(screen.queryByText(/完整内容仍保存在会话中/)).toBeNull();
  expect(screen.getByText('较长的生成中内容暂未完整显示，完成后同步。')).toBeTruthy();
});


it('keeps a visible orphan tool result open and anchored when its earlier parent arrives', async () => {
  const result: MessageRow = { id: 'result', type: 'tool', content: 'currently reading this result',
    tool_call_id: 'call', parent_message_id: 'parent', state: 'complete', provisional: false };
  const parent: MessageRow = { ...row(1), id: 'parent', content: '',
    tool_calls: [{ id: 'call', name: 'read', args: { path: 'sample.txt' } }] };
  let hasParent = false;
  let element: HTMLDivElement | undefined;
  const rectangles = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const top = this.dataset.messageId === 'result' ? (hasParent ? 1200 : 0) - (element?.scrollTop ?? 0) : 0;
    return { top, bottom: top + 500 } as DOMRect;
  });
  const history = control();
  const { container, rerender } = render(<Conversation messages={[result, row(3)]} compacted={false} history={history} />);
  const scroll = element = container.firstElementChild as HTMLDivElement;
  Object.defineProperties(scroll, { clientHeight: { get: () => 100 }, scrollHeight: { get: () => 2400 } });
  scroll.getBoundingClientRect = () => ({ top: 0, bottom: 100 } as DOMRect);
  try {
    scroll.scrollTop = 40;
    await act(async () => fireEvent.scroll(scroll));
    hasParent = true;
    rerender(<Conversation messages={[parent, result, row(3)]} compacted={false} history={history} />);
    const card = container.querySelector('details')!;
    expect(card.open).toBe(true);
    expect(container.querySelector('[data-message-id="result"]')?.getBoundingClientRect().top).toBe(-40);
    expect(scroll.scrollTop).toBe(1240);
    expect(screen.getAllByText(result.content)).toHaveLength(1);
    fireEvent.click(card.querySelector('summary')!);
    expect(card.open).toBe(false);
    rerender(<Conversation messages={[parent, result, row(3), row(4)]} compacted={false} history={history} />);
    expect(card.open).toBe(false);
  } finally { rectangles.mockRestore(); }
});
