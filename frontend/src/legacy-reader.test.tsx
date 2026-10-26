import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import LegacyHistory from './LegacyHistory';
import { LegacyReader } from './legacyReader';
import { browserConnection } from './api';

const connection = { token: 'x'.repeat(40), scopeId: 'legacy', instanceId: 'a'.repeat(32), legacyDatabase: '/selected/old.sqlite' };
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); history.replaceState(null, '', '/'); });
it('preserves the explicit legacy database in the private bootstrap', () => {
  history.replaceState(null, '', '/#' + new URLSearchParams({ access_token: connection.token,
    scope_id: 'legacy', instance_id: connection.instanceId, legacy_database: connection.legacyDatabase }));
  expect(browserConnection()).toEqual(connection);
  expect(location.hash).toBe('');
  expect(browserConnection()).toEqual(connection);
});
it('shows real saved messages without any execution or registration controls', async () => {
  vi.spyOn(LegacyReader.prototype, 'sessions').mockResolvedValue({ root: '/old', execution_root_error: { reason: 'wrong_project' },
    sessions: [{ session_id: 's', model_id: 'A', workspace_root: '/old', state: 'idle', updated_at: '', last_run_status: null }] });
  vi.spyOn(LegacyReader.prototype, 'history').mockResolvedValue({ message_count: 1, checkpoint_id: 'cp' });
  const read = vi.spyOn(LegacyReader.prototype, 'message').mockResolvedValue({ id: 'm', type: 'ai', content: 'saved answer', state: 'complete', provisional: false });
  render(<LegacyHistory connection={connection} />);
  expect(await screen.findByText('saved answer')).toBeDefined();
  expect(screen.getByText(/执行目录不可用（wrong_project）/)).toBeDefined();
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.queryByRole('button', { name: /发送|新建|运行/ })).toBeNull();
  expect(read).toHaveBeenCalledWith('s', 0, 'cp', expect.any(AbortSignal));
});
it('queries only the explicit legacy database, pins the instance, and refuses damaged chunks', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ text: 'part', next_offset: 0, complete: true, sha256: 'f'.repeat(64) })));
  vi.stubGlobal('fetch', fetcher);
  await expect(new LegacyReader(connection).message('s', 0, 'cp', new AbortController().signal)).rejects.toThrow('history_content_invalid');
  const [path, init] = fetcher.mock.calls[0];
  expect(new URL(path, location.origin).searchParams.get('database_path')).toBe(connection.legacyDatabase);
  expect(init.headers['X-Service-Instance']).toBe(connection.instanceId);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it('keeps a failed history read visible and does not retry execution', async () => {
  vi.spyOn(LegacyReader.prototype, 'sessions').mockRejectedValue(new TypeError('disconnected'));
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  render(<LegacyHistory connection={connection} />);
  await waitFor(() => expect(screen.getByRole('alert')).toBeDefined());
  expect(fetcher).not.toHaveBeenCalled();
});
it('distinguishes loading and an actually empty archive without a task welcome', async () => {
  let ready!: (value: { sessions: never[]; root: string; execution_root_error: { reason: string } }) => void;
  vi.spyOn(LegacyReader.prototype, 'sessions').mockImplementation(() => new Promise(resolve => { ready = resolve; }));
  render(<LegacyHistory connection={connection} />);
  expect(screen.getByRole('status').textContent).toContain('正在读取历史');
  expect(screen.queryByText('暂无历史会话')).toBeNull();
  await act(async () => ready({ sessions: [], root: '/old', execution_root_error: { reason: 'missing' } }));
  expect(await screen.findByText('暂无历史会话')).toBeDefined();
  expect(screen.queryByText('今天想完成什么？')).toBeNull();
});
