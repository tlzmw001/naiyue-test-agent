import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Sidebar from './Sidebar';
import type { SessionRow } from '../types';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear(); });
function setup() {
  vi.useFakeTimers(); vi.setSystemTime(new Date(2026, 9, 18, 12));
  const rows: SessionRow[] = [
    ['old', 1], ['today', 18], ['yesterday', 17], ['week', 14],
  ].map(([id, day]) => ({ session_id: String(id), title: String(id), model_id: 'A', workspace_root: null,
    state: 'ready', updated_at: new Date(2026, 9, Number(day), 8).toISOString(), last_run_status: null }));
  const props = { rows, disabled: false, managing: false, open: false, onNew: vi.fn(), onClose: vi.fn(), onSelect: vi.fn(),
    update: vi.fn().mockResolvedValue(true), remove: vi.fn().mockResolvedValue(true), reorder: vi.fn().mockResolvedValue(true) };
  const view = render(<Sidebar {...props} />);
  return { ...view, props, ids: () => Array.from(view.container.querySelectorAll<HTMLElement>('[data-session-id]')).map(e => e.dataset.sessionId) };
}

it('defaults to the saved manual order and only groups by date after an explicit switch', () => {
  const { props, ids } = setup();
  expect(ids()).toEqual(['old', 'today', 'yesterday', 'week']);
  fireEvent.click(screen.getByRole('button', { name: '按日期分组会话' }));
  expect(ids()).toEqual(['today', 'yesterday', 'week', 'old']);
  for (const label of ['今天', '昨天', '近7天', '更早']) expect(screen.getByText(label)).toBeTruthy();
  expect(screen.getByText(/日期视图仅浏览/)).toBeTruthy();
  expect(props.reorder).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '切换到手动顺序' }));
  expect(ids()).toEqual(['old', 'today', 'yesterday', 'week']);
  expect(props.reorder).not.toHaveBeenCalled();
});

it('keeps date-view selection available but never starts a reorder gesture', () => {
  const { props, container } = setup();
  fireEvent.click(screen.getByRole('button', { name: '按日期分组会话' }));
  fireEvent.click(screen.getByRole('button', { name: 'today A' }));
  expect(props.onSelect).toHaveBeenCalledWith('today');
  const row = container.querySelector('[data-session-id="today"]')!;
  fireEvent.pointerDown(row, { button: 0, clientX: 5, clientY: 5 });
  fireEvent.pointerMove(row, { clientX: 100, clientY: 150 });
  fireEvent.pointerUp(row);
  expect(props.reorder).not.toHaveBeenCalled();
});

it('saves the view locally and changes calendar groups after midnight', async () => {
  const { unmount, props } = setup();
  fireEvent.click(screen.getByRole('button', { name: '按日期分组会话' }));
  await act(async () => { await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000); });
  expect(screen.queryByText('今天')).toBeNull();
  expect(screen.getByText('昨天')).toBeTruthy();
  unmount();
  render(<Sidebar {...props} />);
  expect(screen.getByRole('button', { name: '切换到手动顺序' })).toBeTruthy();
});

it('keeps the in-memory selection when browser storage is unavailable', () => {
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage blocked'); });
  const { ids } = setup();
  fireEvent.click(screen.getByRole('button', { name: '按日期分组会话' }));
  expect(ids()[0]).toBe('today');
  expect(warning).toHaveBeenCalledWith('session_view_preference_write_failed');
});
