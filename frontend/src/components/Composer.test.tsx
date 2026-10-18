import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from '../api';
import type { SessionView } from '../types';
import Composer from './Composer';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function setup(overrides: Partial<ComponentProps<typeof Composer>> = {}) {
  const api = new WebApi('composer-fixture');
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: [],
    permission_modes: ['Default'], default_permission_mode: 'Default' });
  const session: SessionView = { session_id: 'one', model_id: 'A', workspace_root: null,
    state: 'ready', updated_at: '', last_run_status: null, messages: [], busy: false, can_run: true,
    has_compaction: false, active_run_id: null, latest_run_id: null, active_tools: [], permission_mode: 'Default',
    manual_review_required: false, pending_approval: false, live: null };
  const props: ComponentProps<typeof Composer> = {
    text: '', pending: [], pendingMode: false, busy: false, stopping: false, sending: false, blocked: false,
    setText: vi.fn(), setPending: vi.fn(), send: vi.fn(), stop: vi.fn(), api, session, controlsDisabled: false,
    selectTools: vi.fn(), selectMode: vi.fn(), openModel: vi.fn(),
    compact: vi.fn(), openContext: vi.fn(), openAgents: vi.fn(), ...overrides,
  };
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<Composer {...props} />); });
  return { props, update: (change: Partial<typeof props>) => {
    Object.assign(props, change);
    view.rerender(<Composer {...props} />);
  } };
}

it('keeps ordinary sending in idle state without a send-mode selector', async () => {
  const { props, update } = await setup();
  expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole('button', { name: '停止运行' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: '发送方式' })).toBeNull();
  update({ text: 'ordinary request' });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  expect(props.send).toHaveBeenCalledExactlyOnceWith(false);
});

it('shows only stop while a running task has no effective draft', async () => {
  const { props, update } = await setup({ busy: true });
  expect(screen.queryByRole('button', { name: '发送消息' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: '发送方式' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '停止运行' }));
  expect(props.stop).toHaveBeenCalledOnce();
  update({ text: '  \n ' });
  expect(screen.queryByRole('button', { name: '发送消息' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: '发送方式' })).toBeNull();
});

it('places the running-draft selector above the editor and sends follow-up by default', async () => {
  const { props } = await setup({ busy: true, text: 'next task' });
  const selector = screen.getByRole('combobox', { name: '发送方式' });
  expect((selector as HTMLSelectElement).value).toBe('follow_up');
  expect(selector.compareDocumentPosition(screen.getByRole('textbox', { name: '消息' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByRole('button', { name: '停止运行' })).toBeNull();
  expect(props.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  expect(props.send).toHaveBeenCalledExactlyOnceWith(true);
});

it('uses steering only when explicitly selected for the current draft', async () => {
  const { props } = await setup({ busy: true, text: 'adjust current task' });
  fireEvent.change(screen.getByRole('combobox', { name: '发送方式' }), { target: { value: 'steering' } });
  expect(props.send).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole('textbox', { name: '消息' }), { key: 'Enter' });
  expect(props.send).toHaveBeenCalledExactlyOnceWith(false);
});

it('waits for confirmed draft clearing before resetting the send mode', async () => {
  const { props, update } = await setup({ busy: true, text: 'keep on failure' });
  fireEvent.change(screen.getByRole('combobox', { name: '发送方式' }), { target: { value: 'steering' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  update({ sending: true });
  expect((screen.getByRole('combobox', { name: '发送方式' }) as HTMLSelectElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  expect(props.send).toHaveBeenCalledOnce();
  update({ sending: false });
  expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('keep on failure');
  expect((screen.getByRole('combobox', { name: '发送方式' }) as HTMLSelectElement).value).toBe('steering');
  update({ text: '' });
  expect(screen.queryByRole('combobox', { name: '发送方式' })).toBeNull();
  expect(screen.getByRole('button', { name: '停止运行' })).toBeTruthy();
  update({ text: 'new draft' });
  expect((screen.getByRole('combobox', { name: '发送方式' }) as HTMLSelectElement).value).toBe('follow_up');
});

it('resets steering when a draft is cleared or another session is selected', async () => {
  const { props, update } = await setup({ busy: true, text: 'draft one' });
  fireEvent.change(screen.getByRole('combobox', { name: '发送方式' }), { target: { value: 'steering' } });
  update({ text: ' ' });
  update({ text: 'replacement draft' });
  expect((screen.getByRole('combobox', { name: '发送方式' }) as HTMLSelectElement).value).toBe('follow_up');
  fireEvent.change(screen.getByRole('combobox', { name: '发送方式' }), { target: { value: 'steering' } });
  update({ session: { ...props.session!, session_id: 'two' }, text: 'draft two' });
  expect((screen.getByRole('combobox', { name: '发送方式' }) as HTMLSelectElement).value).toBe('follow_up');
});

it.each([
  { blocked: true, stopping: false, pendingMode: false },
  { blocked: false, stopping: true, pendingMode: false },
  { blocked: false, stopping: false, pendingMode: true },
])('retains stop for a running task that cannot accept a supplement: %j', async restrictions => {
  const { props } = await setup({ busy: true, text: 'retained draft', ...restrictions });
  expect(screen.queryByRole('combobox', { name: '发送方式' })).toBeNull();
  expect(screen.queryByRole('button', { name: '发送消息' })).toBeNull();
  const stop = screen.getByRole('button', { name: '停止运行' }) as HTMLButtonElement;
  expect(stop.disabled).toBe(restrictions.stopping);
  fireEvent.click(stop);
  expect(props.stop).toHaveBeenCalledTimes(restrictions.stopping ? 0 : 1);
  expect(props.send).not.toHaveBeenCalled();
});

it('preserves explicit resend of retained input after stopping', async () => {
  const { props } = await setup({ pendingMode: true, pending: ['retained text'] });
  const send = screen.getByRole('button', { name: '重新发送消息' }) as HTMLButtonElement;
  expect(send.disabled).toBe(false);
  fireEvent.click(send);
  expect(props.send).toHaveBeenCalledExactlyOnceWith(false);
  expect(screen.getByRole('textbox', { name: '待发送消息 1' })).toBeTruthy();
});

it('keeps composition and Shift+Enter from sending, then uses the selected mode on Enter', async () => {
  const { props } = await setup({ busy: true, text: '输入中的中文' });
  const editor = screen.getByRole('textbox', { name: '消息' });
  fireEvent.keyDown(editor, { key: 'Enter', isComposing: true });
  fireEvent.keyDown(editor, { key: 'Enter', shiftKey: true });
  expect(props.send).not.toHaveBeenCalled();
  fireEvent.keyDown(editor, { key: 'Enter' });
  expect(props.send).toHaveBeenCalledExactlyOnceWith(true);
});

it('grows and shrinks to measured content within the CSS height bounds', async () => {
  const { update } = await setup({ text: 'short' });
  const editor = screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement;
  // jsdom has no layout; supply only the measurements a real textarea provides.
  let measuredHeight = 30;
  Object.defineProperty(editor, 'scrollHeight', { configurable: true, get: () => measuredHeight });
  editor.style.minHeight = '48px';
  editor.style.maxHeight = '180px';
  fireEvent(window, new Event('resize'));
  expect(editor.style.height).toBe('48px');
  measuredHeight = 120;
  update({ text: 'several\nlines\nof text' });
  expect(editor.style.height).toBe('120px');
  expect(editor.style.overflowY).toBe('hidden');
  measuredHeight = 320;
  update({ text: 'many lines\n'.repeat(20) });
  expect(editor.style.height).toBe('180px');
  expect(editor.style.overflowY).toBe('auto');
  measuredHeight = 30;
  update({ text: '' });
  expect(editor.style.height).toBe('48px');
  expect(editor.style.overflowY).toBe('hidden');
});

it('remeasures wrapping on viewport resize without moving the caret', async () => {
  await setup({ text: 'a line that wraps on a narrow viewport' });
  const editor = screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement;
  let measuredHeight = 72;
  Object.defineProperty(editor, 'scrollHeight', { configurable: true, get: () => measuredHeight });
  editor.style.maxHeight = '180px';
  editor.focus();
  editor.setSelectionRange(4, 4);
  fireEvent(window, new Event('resize'));
  expect(editor.style.height).toBe('72px');
  measuredHeight = 144;
  fireEvent(window, new Event('resize'));
  expect(editor.style.height).toBe('144px');
  expect(document.activeElement).toBe(editor);
  expect(editor.selectionStart).toBe(4);
  expect(editor.selectionEnd).toBe(4);
});
