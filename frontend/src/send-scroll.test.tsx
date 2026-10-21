import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import Conversation from './components/Conversation';
import { ApiError, WebApi } from './api';
import type { MessageRow, SessionView } from './types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
const row = (id: string): MessageRow => ({ id, type: 'ai', content: id, state: 'complete', provisional: false });

it.each([false, true])('jumps on a new accepted submission only when enabled=%s', enabled => {
  let height = 2000;
  const props = { messages: [row('old'), row('recent')], compacted: false, scrollAfterSend: enabled };
  const { container, rerender } = render(<Conversation {...props} />);
  const scroll = container.firstElementChild as HTMLDivElement;
  Object.defineProperties(scroll, { clientHeight: { value: 200 }, scrollHeight: { get: () => height } });
  scroll.scrollTop = 500;
  fireEvent.scroll(scroll);
  const lastSubmission = { session_id: 's' };
  rerender(<Conversation {...props} lastSubmission={lastSubmission} />);
  expect(scroll.scrollTop).toBe(enabled ? height : 500);
  if (enabled) expect(screen.queryByRole('button', { name: '回到最新 ↓' })).toBeNull();
  height = 2500;
  rerender(<Conversation {...props} messages={[...props.messages, row('streaming')]} lastSubmission={lastSubmission} />);
  expect(scroll.scrollTop).toBe(enabled ? height : 500);
  scroll.scrollTop = 700;
  fireEvent.scroll(scroll);
  height = 3000;
  rerender(<Conversation {...props} messages={[...props.messages, row('completed')]} lastSubmission={lastSubmission} />);
  expect(scroll.scrollTop).toBe(700);
});

it('changing the preference does not replay an earlier send or react to history rows', () => {
  const lastSubmission = { session_id: 's' };
  const props = { messages: [row('old')], compacted: false, lastSubmission };
  const { container, rerender } = render(<Conversation {...props} scrollAfterSend={false} />);
  const scroll = container.firstElementChild as HTMLDivElement;
  Object.defineProperties(scroll, { clientHeight: { value: 200 }, scrollHeight: { value: 2000 } });
  scroll.scrollTop = 500;
  fireEvent.scroll(scroll);
  rerender(<Conversation {...props} scrollAfterSend messages={[row('earlier'), ...props.messages]} />);
  expect(scroll.scrollTop).toBe(500);
});

function fixture() {
  const api = new WebApi('synthetic-scroll-test');
  const view: SessionView = { session_id: 's', title: '滚动验收', model_id: 'A', workspace_root: null,
    state: 'ready', updated_at: '2026-10-21T00:00:00Z', last_run_status: null, active_tools: [],
    permission_mode: 'Default', messages: [row('reading history'), row('recent answer')], busy: false,
    can_run: true, has_compaction: false, active_run_id: null, latest_run_id: null,
    manual_review_required: false, pending_approval: false, live: null };
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([view]);
  vi.spyOn(api, 'session').mockResolvedValue(view);
  vi.spyOn(api, 'prepare').mockResolvedValue(view);
  vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: [],
    permission_modes: ['Default'], default_permission_mode: 'Default' });
  vi.spyOn(api, 'run').mockResolvedValue({ session_id: 's', run_id: 'r', status: 'completed',
    messages: [...view.messages, { ...row('new request'), type: 'human' }], last_sequence: 0,
    incomplete: false, done: true, manual_review_required: false });
  vi.spyOn(api, 'events').mockResolvedValue(undefined);
  const start = vi.spyOn(api, 'start').mockResolvedValue({ session_id: 's', run_id: 'r' });
  return { api, start };
}

it.each(['keep', 'latest', 'failure'])('wires the real composer to scrolling: %s', async mode => {
  if (mode !== 'keep') localStorage.setItem('naiyue.scroll-after-send', 'latest');
  const { api, start } = fixture();
  if (mode === 'failure') start.mockRejectedValue(new ApiError('session_busy'));
  const { container } = render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '滚动验收 A' }));
  await screen.findByText('reading history');
  const scroll = container.querySelector('[data-message-id="reading history"]')!.closest('article')!.parentElement!.parentElement as HTMLDivElement;
  Object.defineProperties(scroll, { clientHeight: { value: 200 }, scrollHeight: { value: 2400 } });
  scroll.scrollTop = 500;
  fireEvent.scroll(scroll);
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'new request' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
  if (mode === 'failure') {
    await waitFor(() => expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(false));
    expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('new request');
  } else {
    await waitFor(() => expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe(''));
    expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(true);
  }
  expect(scroll.scrollTop).toBe(mode === 'latest' ? 2400 : 500);
});

it('places the preference in the plus menu, persists it and keeps the header clear', async () => {
  const { api, start } = fixture();
  const { unmount } = render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  fireEvent.click(screen.getByRole('button', { name: '指令与功能' }));
  expect(screen.queryByRole('menuitemradio')).toBeNull();
  const parent = screen.getByRole('menuitem', { name: '发送后滚动' });
  expect(parent.getAttribute('aria-expanded')).toBe('false');
  fireEvent.mouseEnter(parent);
  expect(parent.getAttribute('aria-expanded')).toBe('true');
  const latest = screen.getByRole('menuitemradio', { name: '回到最新消息' });
  expect(latest.closest('form')).toBe(screen.getByRole('textbox', { name: '消息' }).closest('form'));
  expect(document.querySelector('header')?.textContent).not.toContain('发送后滚动');
  expect(screen.queryByRole('button', { name: /发送后滚动：/ })).toBeNull();
  expect(screen.getByRole('menuitemradio', { name: '保持当前位置' }).getAttribute('aria-checked')).toBe('true');
  fireEvent.click(latest);
  expect(localStorage.getItem('naiyue.scroll-after-send')).toBe('latest');
  expect(screen.queryByRole('menu')).toBeNull();
  expect(start).not.toHaveBeenCalled();
  unmount();
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  fireEvent.click(screen.getByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '发送后滚动' }));
  expect(screen.getByRole('menuitemradio', { name: '回到最新消息' }).getAttribute('aria-checked')).toBe('true');
  expect(screen.getByRole('button', { name: '主题：跟随系统' })).toBeTruthy();
});

it('falls back for invalid storage and keeps selection usable after storage failure', async () => {
  localStorage.setItem('naiyue.scroll-after-send', 'invalid');
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('PRIVATE'); });
  const { api } = fixture();
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  fireEvent.click(screen.getByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '发送后滚动' }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: '回到最新消息' }));
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  fireEvent.click(screen.getByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '发送后滚动' }));
  expect(screen.getByRole('menuitemradio', { name: '回到最新消息' }).getAttribute('aria-checked')).toBe('true');
  expect(warn).toHaveBeenCalledWith('send_scroll_preference_write_failed');
});

it('diagnoses storage reads and supports keyboard and outside closing', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('PRIVATE'); });
  const { api } = fixture();
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  const trigger = screen.getByRole('button', { name: '指令与功能' });
  fireEvent.click(trigger);
  const main = screen.getByRole('menu', { name: '指令与功能' });
  fireEvent.keyDown(main, { key: 'End' });
  const parent = screen.getByRole('menuitem', { name: '发送后滚动' });
  expect(document.activeElement).toBe(parent);
  fireEvent.keyDown(parent, { key: 'ArrowRight' });
  const submenu = screen.getByRole('menu', { name: '发送后滚动' });
  expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: '保持当前位置' }));
  fireEvent.keyDown(submenu, { key: 'End' });
  expect(document.activeElement?.textContent).toBe('回到最新消息');
  fireEvent.keyDown(submenu, { key: 'ArrowDown' });
  expect(document.activeElement?.textContent).toBe('保持当前位置✓');
  fireEvent.keyDown(submenu, { key: 'ArrowLeft' });
  expect(screen.queryByRole('menu', { name: '发送后滚动' })).toBeNull();
  expect(document.activeElement).toBe(parent);
  fireEvent.keyDown(parent, { key: 'ArrowRight' });
  fireEvent.keyDown(screen.getByRole('menu', { name: '发送后滚动' }), { key: 'Escape' });
  expect(document.activeElement).toBe(parent);
  expect(screen.getByRole('menu', { name: '指令与功能' })).toBe(main);
  fireEvent.keyDown(parent, { key: 'Escape' });
  expect(document.activeElement).toBe(trigger);
  fireEvent.click(trigger);
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('menu')).toBeNull();
  expect(warn).toHaveBeenCalledWith('send_scroll_preference_read_failed');
});


it('keeps the flyout open across its options, closes on leave and resets when reopening', async () => {
  const { api, start } = fixture();
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  const plus = screen.getByRole('button', { name: '指令与功能' });
  fireEvent.click(plus);
  const parent = screen.getByRole('menuitem', { name: '发送后滚动' });
  fireEvent.mouseEnter(parent);
  const option = screen.getByRole('menuitemradio', { name: '回到最新消息' });
  fireEvent.mouseLeave(parent, { relatedTarget: option });
  fireEvent.mouseEnter(option, { relatedTarget: parent });
  expect(screen.getByRole('menu', { name: '发送后滚动' })).toBeTruthy();
  fireEvent.mouseLeave(parent.parentElement!, { relatedTarget: document.body });
  expect(screen.queryByRole('menu', { name: '发送后滚动' })).toBeNull();
  fireEvent.click(parent);
  expect(screen.getByRole('menu', { name: '发送后滚动' })).toBeTruthy();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('menu')).toBeNull();
  fireEvent.click(plus);
  expect(screen.queryByRole('menu', { name: '发送后滚动' })).toBeNull();
  expect(screen.getByRole('menuitem', { name: '发送后滚动' }).getAttribute('aria-expanded')).toBe('false');
  expect(start).not.toHaveBeenCalled();
});
