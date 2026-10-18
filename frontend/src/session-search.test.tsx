import type { ComponentProps } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Sidebar from './components/Sidebar';
import App from './App';
import { WebApi } from './api';
import type { SessionRow, SessionView } from './types';

afterEach(cleanup);
const rows: SessionRow[] = [
  { session_id: 'pinned', title: '接口回归 Alpha', pinned: true, model_id: 'A', workspace_root: null,
    state: 'ready', updated_at: '2026-10-17T12:00:00Z', last_run_status: 'completed' },
  { session_id: 'other', title: '支付测试', pinned: false, model_id: 'B', workspace_root: null,
    state: 'interrupted', updated_at: '2026-10-17T11:00:00Z', last_run_status: 'interrupted' },
  { session_id: 'last', title: '接口回归 beta', pinned: false, model_id: 'A', workspace_root: null,
    state: 'ready', updated_at: '2026-10-17T10:00:00Z', last_run_status: 'completed' },
];

function setup(overrides: Partial<ComponentProps<typeof Sidebar>> = {}) {
  const props: ComponentProps<typeof Sidebar> = { rows, open: false, disabled: false, managing: false,
    onNew: vi.fn(), onSelect: vi.fn(), onClose: vi.fn(), update: vi.fn(async () => true),
    remove: vi.fn(async () => true), reorder: vi.fn(async () => true), ...overrides };
  const view = render(<Sidebar {...props} />);
  return { props, unmount: view.unmount, update: (change: Partial<typeof props>) => {
    Object.assign(props, change); view.rerender(<Sidebar {...props} />);
  } };
}
function open() {
  fireEvent.click(screen.getByRole('button', { name: '搜索会话' }));
  return screen.getByRole('dialog', { name: '搜索会话' });
}
function search(value: string) {
  fireEvent.change(screen.getByRole('combobox', { name: '搜索会话标题' }), { target: { value } });
}

it('opens from the sidebar and searches literal titles locally in the original order', () => {
  const { props } = setup();
  const dialog = open();
  const input = within(dialog).getByRole('combobox', { name: '搜索会话标题' });
  expect(document.activeElement).toBe(input);
  expect(within(dialog).getAllByRole('option').map(item => item.textContent)).toEqual([
    '接口回归 AlphaA已置顶', '支付测试B未完成', '接口回归 betaA',
  ]);
  search(' 接口回归 ');
  expect(within(dialog).getAllByRole('option').map(item => item.textContent)).toEqual(['接口回归 AlphaA已置顶', '接口回归 betaA']);
  search('ALPHA');
  expect(within(dialog).getAllByRole('option')).toHaveLength(1);
  expect(props.onSelect).not.toHaveBeenCalled();
  expect(props.onNew).not.toHaveBeenCalled();
  expect(props.update).not.toHaveBeenCalled();
  expect(props.remove).not.toHaveBeenCalled();
  expect(props.reorder).not.toHaveBeenCalled();
});

it.each([{ metaKey: true }, { ctrlKey: true }])('opens from the global search shortcut even when mobile navigation is closed: %j', modifier => {
  setup({ open: false });
  fireEvent.keyDown(window, { key: 'k', ...modifier });
  expect(screen.getByRole('dialog', { name: '搜索会话' })).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole('combobox', { name: '搜索会话标题' }));
});

it('uses arrows and Enter to choose the exact session, then closes', () => {
  const { props } = setup();
  open(); search('接口');
  const input = screen.getByRole('combobox', { name: '搜索会话标题' });
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  const second = screen.getByRole('option', { name: /接口回归 beta/ });
  expect(second.getAttribute('aria-selected')).toBe('true');
  expect(input.getAttribute('aria-activedescendant')).toBe(second.id);
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(props.onSelect).toHaveBeenCalledExactlyOnceWith('last');
  expect(screen.queryByRole('dialog', { name: '搜索会话' })).toBeNull();
});

it('supports mouse selection and cancellation with focus restored', () => {
  const { props } = setup();
  const trigger = screen.getByRole('button', { name: '搜索会话' });
  trigger.focus(); open(); search('支付');
  fireEvent.click(screen.getByRole('option', { name: /支付测试/ }));
  expect(props.onSelect).toHaveBeenCalledExactlyOnceWith('other');
  trigger.focus(); open(); search('接口');
  fireEvent.keyDown(screen.getByRole('combobox', { name: '搜索会话标题' }), { key: 'Escape' });
  expect(document.activeElement).toBe(trigger);
  expect(screen.queryByRole('dialog')).toBeNull();
  open();
  fireEvent.click(screen.getByRole('button', { name: '关闭搜索' }));
  expect(document.activeElement).toBe(trigger);
  expect(props.onSelect).toHaveBeenCalledTimes(1);
});

it('contains Tab focus and ignores composing Enter or shortcut keys', () => {
  setup();
  fireEvent.keyDown(window, { key: 'k', metaKey: true, isComposing: true });
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true, altKey: true });
  expect(screen.queryByRole('dialog')).toBeNull();
  open();
  const input = screen.getByRole('combobox', { name: '搜索会话标题' });
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
  expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.keyDown(input, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '关闭搜索' }));
  fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
  expect(document.activeElement).toBe(input);
});

it('restores the original menu trigger when searching from a session menu and reopening the shortcut', () => {
  setup();
  const trigger = screen.getByRole('button', { name: '接口回归 Alpha的更多操作' });
  trigger.focus(); fireEvent.click(trigger);
  expect(screen.getByRole('menu', { name: '会话操作' })).toBeTruthy();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  expect(screen.queryByRole('menu', { name: '会话操作' })).toBeNull();
  const input = screen.getByRole('combobox', { name: '搜索会话标题' });
  expect(document.activeElement).toBe(input);
  fireEvent.keyDown(input, { key: 'k', ctrlKey: true });
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(document.activeElement).toBe(trigger);
});

it('shows empty and no-match states without treating a query as a regular expression or model search', () => {
  const { props, update } = setup();
  open(); search('.*');
  expect(screen.getByText('没有匹配的会话')).toBeTruthy();
  fireEvent.keyDown(screen.getByRole('combobox', { name: '搜索会话标题' }), { key: 'Enter' });
  expect(props.onSelect).not.toHaveBeenCalled();
  search('B');
  expect(screen.getAllByRole('option')).toHaveLength(1);
  expect(screen.getByRole('option').textContent).toContain('beta');
  update({ rows: [] });
  expect(screen.getByText('当前没有可搜索的会话')).toBeTruthy();
});

it.each([{ disabled: true }, { managing: true }])('preserves the existing selection gate: %j', restrictions => {
  const { props } = setup(restrictions);
  open();
  expect((screen.getAllByRole('option')[0] as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getAllByRole('option')[0]);
  fireEvent.keyDown(screen.getByRole('combobox', { name: '搜索会话标题' }), { key: 'Enter' });
  expect(props.onSelect).not.toHaveBeenCalled();
});

it('uses current rows after rename, deletion and reordering without stale selection', () => {
  const { props, update } = setup();
  open(); search('接口');
  const input = screen.getByRole('combobox', { name: '搜索会话标题' });
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  update({ rows: [{ ...rows[0], title: '新名称' }] });
  expect(screen.getByText('没有匹配的会话')).toBeTruthy();
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(props.onSelect).not.toHaveBeenCalled();
  search('');
  update({ rows: [rows[2], rows[0]] });
  expect(screen.getAllByRole('option').map(item => item.textContent)).toEqual(['接口回归 betaA', '接口回归 AlphaA已置顶']);
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(props.onSelect).toHaveBeenCalledExactlyOnceWith('last');
});

it('escapes titles, supports the existing untitled fallback and cleans up on unmount', () => {
  const { unmount } = setup({ rows: [{ ...rows[0], title: '<img src=x onerror=alert(1)>' }, { ...rows[1], title: null }] });
  open();
  expect(screen.getByRole('dialog').querySelector('img')).toBeNull();
  expect(screen.getAllByRole('option')).toHaveLength(2);
  expect(screen.getAllByRole('option')[1].textContent).toMatch(/10\/17/);
  unmount();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('does not read or execute a session until a result is chosen through the real App', async () => {
  const api = new WebApi('search-fixture');
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue(rows);
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: [], permission_modes: ['Default'], default_permission_mode: 'Default' });
  const value = { ...rows[2], messages: [], busy: false, can_run: true,
    active_tools: [], permission_mode: 'Default', has_compaction: false, active_run_id: null, latest_run_id: null,
    pending_approval: false, manual_review_required: false, live: null } as SessionView;
  const session = vi.spyOn(api, 'session').mockResolvedValue(value);
  const prepare = vi.spyOn(api, 'prepare').mockResolvedValue(value);
  vi.spyOn(api, 'context').mockRejectedValue(new Error('fixture preview unavailable'));
  const create = vi.spyOn(api, 'create'), start = vi.spyOn(api, 'start');
  render(<App api={api} />);
  await screen.findByRole('button', { name: '接口回归 beta A' });
  await act(async () => fireEvent.keyDown(window, { key: 'k', ctrlKey: true }));
  search('beta');
  expect(session).not.toHaveBeenCalled();
  expect(prepare).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('option', { name: /接口回归 beta/ }));
  await waitFor(() => expect(prepare).toHaveBeenCalledExactlyOnceWith('last'));
  expect(create).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
});
