import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import DirectoryBrowser from './DirectoryBrowser';
import type { DirectoryPage } from '../projectTypes';
afterEach(cleanup);
it('never replaces a path typed while the initial directory request is pending', async () => {
  let release!: (value: DirectoryPage) => void;
  const api = { browseDirectories: vi.fn(() => new Promise<DirectoryPage>(resolve => { release = resolve; })) };
  render(<DirectoryBrowser api={api} mode="select" purpose="save" choose={vi.fn()} close={vi.fn()} />);
  const input = screen.getByRole('textbox', { name: '文件夹路径' }); fireEvent.change(input, { target: { value: '/reports' } });
  await act(async () => release({ path: '/default', identity: 'old', parent: '/', separator: '/', default_parent_dir: '/default', default_error: null, positions: [], items: [], next_offset: null }));
  expect(input).toHaveProperty('value', '/reports');
  expect(screen.getByRole('button', { name: '选择此文件夹' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '前往' }));
  expect(api.browseDirectories).toHaveBeenLastCalledWith({ path: '/reports', show_hidden: false }, expect.any(AbortSignal));
});
