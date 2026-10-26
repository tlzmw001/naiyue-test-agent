import type { ComponentProps } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { RunView, SessionRow, SessionView } from '../types';
import Sidebar from './Sidebar';

afterEach(cleanup);
const rows: SessionRow[] = [
  { session_id: 'pinned', title: '接口回归', pinned: true, model_id: 'model-a', workspace_root: null,
    state: 'ready', updated_at: '2026-10-17T12:00:00Z', last_run_status: 'completed' },
  { session_id: 'pending', title: '支付检查', pinned: false, model_id: 'model-b', workspace_root: null,
    state: 'interrupted', updated_at: '2026-10-17T11:00:00Z', last_run_status: 'interrupted' },
];

function setup(overrides: Partial<ComponentProps<typeof Sidebar>> = {}) {
  const props: ComponentProps<typeof Sidebar> = { rows, selected: 'pinned', open: false, disabled: false, managing: false,
    onNew: vi.fn(), onSelect: vi.fn(), onClose: vi.fn(), update: vi.fn(async () => true),
    remove: vi.fn(async () => true), reorder: vi.fn(async () => true), ...overrides };
  const view = render(<Sidebar {...props} />);
  return { props, ...view };
}

it('identifies sandbox and host bindings without guessing from a missing directory', () => {
  const { container } = setup({ rows: [
    { ...rows[0], execution_binding: 'sandbox' },
    { ...rows[1], execution_binding: 'project_local' },
    { ...rows[0], session_id: 'chat', pinned: false, title: '普通对话', execution_binding: null },
  ] });
  const sandbox = screen.getByRole('button', { name: '接口回归 model-a' });
  expect(within(sandbox).getByLabelText('执行位置：Docker 沙箱')).toBeTruthy();
  expect(within(sandbox).getByRole('img', { name: '已置顶' })).toBeTruthy();
  expect(within(screen.getByRole('button', { name: '支付检查 model-b' })).getByLabelText('执行位置：此计算机')).toBeTruthy();
  expect(within(screen.getByRole('button', { name: '普通对话 model-a' })).queryByLabelText(/执行位置/)).toBeNull();
  expect(container.querySelectorAll('[aria-label="执行位置：Docker 沙箱"]')).toHaveLength(1);
  expect(document.getElementById(sandbox.getAttribute('aria-describedby')!)?.textContent).toContain('已完成');
});

it.each([
  ['running', 'completed', '运行中'], ['ready', 'completed', '已完成'], ['ready', 'stopped', '已停止'],
  ['failed', 'completed', '失败'], ['interrupted', 'completed', '待继续'], ['review_required', 'completed', '待核对'],
])('shows saved %s status without promoting an old %s result', (state, last_run_status, label) => {
  setup({ rows: [{ ...rows[0], state, last_run_status, execution_binding: 'sandbox' }] });
  expect(screen.getByLabelText(`任务状态：${label}`)).toBeTruthy();
  if (label !== '已完成') expect(screen.queryByLabelText('任务状态：已完成')).toBeNull();
});

it('updates live run and approval facts immediately and ignores another session with a stale result', () => {
  const current = { ...rows[0], pending_approval: false, execution: { kind: 'sandbox', state: 'ready' } } as SessionView;
  const run: RunView = { run_id: 'r', session_id: rows[0].session_id, status: 'running', messages: [], done: false,
    last_sequence: 1, incomplete: false, manual_review_required: false };
  const { props, rerender } = setup({ rows: [rows[0]], currentSession: current, currentRun: run });
  expect(screen.getByLabelText('任务状态：运行中')).toBeTruthy();
  rerender(<Sidebar {...props} currentSession={{ ...current, pending_approval: true }} />);
  expect(screen.getByLabelText('任务状态：等待审批')).toBeTruthy();
  rerender(<Sidebar {...props} currentSession={{ ...current, session_id: 'foreign', pending_approval: true }}
    currentRun={{ ...run, session_id: 'foreign' }} />);
  expect(screen.getByLabelText('任务状态：已完成')).toBeTruthy();
  expect(screen.queryByLabelText('任务状态：等待审批')).toBeNull();
  expect(screen.queryByLabelText('执行位置：Docker 沙箱')).toBeNull();
  rerender(<Sidebar {...props} currentRun={undefined} currentSession={{ ...current, live: { ...run, session_id: 'foreign' } }} />);
  expect(screen.getByLabelText('任务状态：已完成')).toBeTruthy();
  expect(screen.queryByLabelText('任务状态：运行中')).toBeNull();
});

it('keeps completed history complete when its sandbox is unavailable and leaves unknown facts blank', () => {
  const current = { ...rows[0], execution: { kind: 'sandbox', state: 'unavailable' } } as SessionView;
  const { props, rerender } = setup({ rows: [rows[0]], currentSession: current });
  expect(screen.getByLabelText('任务状态：已完成')).toBeTruthy();
  rerender(<Sidebar {...props} rows={[{ ...rows[0], state: 'unknown', last_run_status: 'unknown' }]} currentSession={undefined} />);
  expect(screen.queryByLabelText(/任务状态/)).toBeNull();
  expect(screen.queryByLabelText(/执行位置/)).toBeNull();
});

it('does not leave an unfinished tooltip or accessible name after the live run completes before the list refresh', () => {
  const run: RunView = { run_id: 'r', session_id: rows[0].session_id, status: 'completed', messages: [], done: true,
    last_sequence: 2, incomplete: false, manual_review_required: false };
  setup({ rows: [{ ...rows[0], state: 'running' }], currentRun: run });
  const button = screen.getByRole('button', { name: /接口回归 model-a/ });
  expect(button.getAttribute('aria-label')).toBe('接口回归 model-a');
  expect(button.getAttribute('title')).toBe('接口回归 · model-a');
  expect(within(button).getByLabelText('任务状态：已完成')).toBeTruthy();
});

it('puts search before new conversation and marks only pinned rows on the left', () => {
  const { container } = setup();
  const search = screen.getByRole('button', { name: '搜索会话' });
  const create = screen.getByRole('button', { name: '新建会话' });
  expect(search.compareDocumentPosition(create) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const pinned = screen.getByRole('button', { name: '接口回归 model-a' });
  const marker = within(pinned).getByRole('img', { name: '已置顶' });
  expect(marker.closest('[data-session-id]')?.getAttribute('data-session-id')).toBe('pinned');
  expect(within(screen.getByRole('button', { name: '支付检查 model-b' })).queryByRole('img', { name: '已置顶' })).toBeNull();
  expect(container.querySelectorAll('[role="img"][aria-label="已置顶"]')).toHaveLength(1);
});

it.each(['chats', 'project-b'])('rejects a foreign %s drop even when session IDs overlap', scopeId => {
  const { props } = setup({ rows: rows.map(row => ({ ...row, pinned: false })) });
  const source = screen.getByRole('button', { name: '支付检查 model-b' });
  const foreign = document.createElement('div');
  foreign.dataset.sessionScope = scopeId;
  const target = document.createElement('div');
  target.dataset.sessionId = 'pinned'; foreign.append(target); document.body.append(foreign);
  // Both rows are deliberately unpinned, so only scope ownership can reject this drop.
  const original = document.elementFromPoint;
  document.elementFromPoint = () => target;
  try {
    fireEvent(source, new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 50, clientY: 200 }));
    fireEvent(source, new MouseEvent('pointermove', { bubbles: true, clientX: 50, clientY: 20 }));
    fireEvent(source, new MouseEvent('pointerup', { bubbles: true, clientX: 50, clientY: 20 }));
  } finally { document.elementFromPoint = original; foreign.remove(); }
  expect(props.reorder).not.toHaveBeenCalled();
  expect(props.update).not.toHaveBeenCalled();
  expect(props.onSelect).not.toHaveBeenCalled();
});

it('shows one title summary and moves model details to the tooltip without losing accessible information', () => {
  const { props } = setup();
  const ready = screen.getByRole('button', { name: '接口回归 model-a' });
  const pending = screen.getByRole('button', { name: '支付检查 model-b' });
  expect(ready.textContent).toBe('接口回归已完成');
  expect(ready.getAttribute('title')).toBe('接口回归 · model-a');
  expect(pending.textContent).toBe('支付检查待继续');
  expect(pending.getAttribute('title')).toBe('支付检查 · model-b');
  expect(ready.getAttribute('aria-current')).toBe('true');
  expect(pending.getAttribute('aria-current')).toBeNull();
  fireEvent.click(pending);
  expect(props.onSelect).toHaveBeenCalledExactlyOnceWith('pending');
  expect(props.update).not.toHaveBeenCalled();
  expect(props.reorder).not.toHaveBeenCalled();
});

it('preserves complete escaped titles, untitled fallback and existing pin groups', () => {
  const title = '<img src=x onerror=alert(1)>' + '长标题'.repeat(40);
  const { container } = setup({ rows: [{ ...rows[0], title }, { ...rows[1], title: null }] });
  const pinned = within(screen.getByRole('group', { name: '已置顶会话' })).getByRole('button', { name: `${title} model-a` });
  expect(pinned.textContent).toBe(title + '已完成');
  expect(pinned.getAttribute('title')).toBe(`${title} · model-a`);
  const regular = within(screen.getByRole('group', { name: '普通会话' })).getByRole('button', { name: /model-b/ });
  expect(regular.textContent).toMatch(/10\/17.*待继续/);
  expect(regular.textContent).not.toContain('model-b');
  expect(container.querySelector('img')).toBeNull();
  expect(Array.from(container.querySelectorAll('[data-session-id]'), item => (item as HTMLElement).dataset.sessionId)).toEqual(['pinned', 'pending']);
});

it('folds and expands locally while retaining the original list, selected session and order', () => {
  const { props, container } = setup();
  const navigation = screen.getByRole('complementary', { name: '会话导航' });
  const list = navigation.querySelector('nav')!;
  const before = Array.from(container.querySelectorAll('[data-session-id]'));
  const fold = screen.getByRole('button', { name: '收起侧栏' });
  expect(fold.getAttribute('aria-expanded')).toBe('true');
  expect(fold.getAttribute('aria-controls')).toBe(list.id);
  expect(list.id).not.toBe('');
  expect(navigation.getAttribute('data-collapsed')).toBe('false');
  fireEvent.click(fold);
  const expand = screen.getByRole('button', { name: '展开侧栏' });
  expect(expand.getAttribute('aria-expanded')).toBe('false');
  expect(navigation.getAttribute('data-collapsed')).toBe('true');
  expect(document.activeElement).toBe(expand);
  expect(navigation.querySelector('nav')).toBe(list);
  expect(Array.from(container.querySelectorAll('[data-session-id]'))).toEqual(before);
  expect(screen.getByRole('button', { name: '接口回归 model-a' }).getAttribute('aria-current')).toBe('true');
  fireEvent.click(expand);
  expect(navigation.getAttribute('data-collapsed')).toBe('false');
  for (const callback of [props.onNew, props.onSelect, props.onClose, props.update, props.remove, props.reorder]) {
    expect(callback).not.toHaveBeenCalled();
  }
});

it('keeps new and search actions available in the rail and restores search focus on cancellation', () => {
  const { props } = setup();
  fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
  fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
  expect(props.onNew).toHaveBeenCalledTimes(1);
  const search = screen.getByRole('button', { name: '搜索会话' });
  search.focus(); fireEvent.click(search);
  fireEvent.keyDown(screen.getByRole('combobox', { name: '搜索会话标题' }), { key: 'Escape' });
  expect(document.activeElement).toBe(search);
  fireEvent.click(search);
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '支付' } });
  fireEvent.click(screen.getByRole('option', { name: /支付检查/ }));
  expect(props.onSelect).toHaveBeenCalledExactlyOnceWith('pending');
});

it.each([{ disabled: true }, { managing: true }])('keeps action gates while allowing presentation to fold: %j', restrictions => {
  const { props } = setup(restrictions);
  fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
  expect(screen.getByRole('button', { name: '展开侧栏' })).toBeTruthy();
  const create = screen.getByRole('button', { name: '新建会话' }) as HTMLButtonElement;
  expect(create.disabled).toBe(true);
  fireEvent.click(create);
  fireEvent.click(screen.getByRole('button', { name: '搜索会话' }));
  const candidate = screen.getAllByRole('option')[0] as HTMLButtonElement;
  expect(candidate.disabled).toBe(true);
  fireEvent.click(candidate);
  fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Enter' });
  expect(props.onNew).not.toHaveBeenCalled();
  expect(props.onSelect).not.toHaveBeenCalled();
});

it('closes the session menu without restoring focus to a hidden row when folding', () => {
  setup();
  const more = screen.getByRole('button', { name: '接口回归的更多操作' });
  more.focus(); fireEvent.click(more);
  expect(screen.getByRole('menu', { name: '会话操作' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
  expect(screen.queryByRole('menu', { name: '会话操作' })).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '展开侧栏' }));
});

it('preserves mobile open and close callbacks independently of the local desktop folding state', () => {
  const { props, rerender } = setup();
  fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
  rerender(<Sidebar {...props} open />);
  fireEvent.click(screen.getByRole('button', { name: '关闭会话栏' }));
  fireEvent.click(screen.getByRole('button', { name: '关闭导航' }));
  expect(props.onClose).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('complementary').getAttribute('data-collapsed')).toBe('true');
  rerender(<Sidebar {...props} open={false} />);
  expect(screen.queryByRole('button', { name: '关闭会话栏' })).toBeNull();
  expect(screen.getByRole('button', { name: '支付检查 model-b' })).toBeTruthy();
});
