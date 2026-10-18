import type { ComponentProps } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { SessionRow } from '../types';
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

it('shows one title summary and moves model details to the tooltip without losing accessible information', () => {
  const { props } = setup();
  const ready = screen.getByRole('button', { name: '接口回归 model-a' });
  const pending = screen.getByRole('button', { name: '支付检查 model-b · 未完成' });
  expect(ready.textContent).toBe('接口回归');
  expect(ready.getAttribute('title')).toBe('接口回归 · model-a');
  expect(pending.textContent).toBe('支付检查未完成');
  expect(pending.getAttribute('title')).toBe('支付检查 · model-b · 未完成');
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
  expect(pinned.textContent).toBe(title);
  expect(pinned.getAttribute('title')).toBe(`${title} · model-a`);
  const regular = within(screen.getByRole('group', { name: '普通会话' })).getAllByRole('button')[0];
  expect(regular.textContent).toMatch(/10\/17.*未完成/);
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
  expect(screen.getByRole('button', { name: '支付检查 model-b · 未完成' })).toBeTruthy();
});
