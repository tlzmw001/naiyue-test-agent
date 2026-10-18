import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { WebApi } from './api';

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); delete document.documentElement.dataset.theme; });
function setup() {
  const api = new WebApi('theme-fixture');
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: [], permission_modes: ['Default'], default_permission_mode: 'Default' });
  const start = vi.spyOn(api, 'start');
  const view = render(<App api={api} />);
  return { ...view, api, start };
}

it('defaults to system, selects dark and restores the persisted choice without a model request', async () => {
  const { unmount, api, start } = setup();
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  const trigger = screen.getByRole('button', { name: '主题：跟随系统' });
  expect(document.documentElement.dataset.theme).toBe('system');
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole('menuitemradio', { name: '深色' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(localStorage.getItem('naiyue.theme')).toBe('dark');
  expect(screen.queryByRole('menu')).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(api.models).toHaveBeenCalledTimes(1); expect(start).not.toHaveBeenCalled();
  unmount();
  setup();
  expect(screen.getByRole('button', { name: '主题：深色' })).toBeTruthy();
  expect(document.documentElement.dataset.theme).toBe('dark');
});

it('supports light and system modes and closes the menu with Escape', () => {
  localStorage.setItem('naiyue.theme', 'light');
  setup();
  const trigger = screen.getByRole('button', { name: '主题：浅色' });
  fireEvent.click(trigger);
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' });
  expect(document.activeElement?.getAttribute('role')).toBe('menuitemradio');
  fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
  expect(document.activeElement).toBe(trigger);
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole('menuitemradio', { name: '跟随系统' }));
  expect(document.documentElement.dataset.theme).toBe('system');
});

it('uses system for an unknown saved choice', () => {
  localStorage.setItem('naiyue.theme', 'invalid'); setup();
  expect(screen.getByRole('button', { name: '主题：跟随系统' })).toBeTruthy();
});

it('keeps theme selection usable when browser preference IO fails and records the failures', () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  setup();
  fireEvent.click(screen.getByRole('button', { name: '主题：跟随系统' }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: '深色' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(warn).toHaveBeenCalledWith('theme_preference_read_failed');
  expect(warn).toHaveBeenCalledWith('theme_preference_write_failed');
});
