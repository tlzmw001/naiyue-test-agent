import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { WebApi } from '../api';
import ExecutionSelector from './ExecutionSelector';
import type { ExecutionOptions } from '../types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const options: ExecutionOptions = { host_available: true, sandbox_available: true, requires_trust: false,
  project_revision: 1, error_code: null, network: 'none', untracked: ['notes.txt', '.env'], blocked: ['.env'],
  default_profile: 'python', profiles: [{ profile_id: 'python', name: 'Python tests', revision: 'a'.repeat(64) },
    { profile_id: 'node', name: 'Node tests', revision: 'b'.repeat(64) }] };
function setup() {
  const api = new WebApi('synthetic', '11111111-1111-1111-1111-111111111111');
  vi.spyOn(api, 'executionOptions').mockResolvedValue(options);
  vi.spyOn(api, 'refreshEnvironments').mockResolvedValue({});
  const create = vi.fn().mockResolvedValue(undefined);
  render(<ExecutionSelector api={api} session={null} disabled={false} create={create} refresh={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '执行位置：此计算机' }));
  return { api, create };
}
it('shows configured templates and default marks and confirms only a draft', async () => {
  const { create } = setup();
  expect(await screen.findByRole('button', { name: 'Python tests' })).toBeTruthy();
  expect(screen.getByText('默认')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Node tests' }));
  expect(screen.getByText(/发送第一条消息/)).toBeTruthy();
  expect(screen.getByRole('checkbox', { name: '.env' }).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: 'notes.txt' }));
  fireEvent.click(screen.getByRole('button', { name: '使用此环境' }));
  await waitFor(() => expect(create).toHaveBeenCalledWith({ execution: 'sandbox', include_untracked: ['notes.txt'],
    profile_id: 'node', profile_name: 'Node tests', expected_profile_revision: 'b'.repeat(64) }));
});
it('refreshes configured templates and keeps the last list if refresh fails', async () => {
  const { api } = setup();
  await screen.findByRole('button', { name: 'Python tests' });
  vi.mocked(api.executionOptions).mockResolvedValue({ ...options, profiles: [options.profiles![1]], default_profile: 'node' });
  fireEvent.click(screen.getByRole('button', { name: '刷新环境配置' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Python tests' })).toBeNull());
  expect(screen.getByRole('button', { name: 'Node tests' })).toBeTruthy();
  vi.mocked(api.refreshEnvironments).mockRejectedValue(new Error('offline'));
  fireEvent.click(screen.getByRole('button', { name: '刷新环境配置' }));
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: 'Node tests' })).toBeTruthy();
});
it('does not invent sandbox choices for an empty catalog', async () => {
  const { api } = setup();
  await screen.findByRole('button', { name: 'Python tests' });
  vi.mocked(api.executionOptions).mockResolvedValue({ ...options, profiles: [], default_profile: null, sandbox_available: false });
  fireEvent.click(screen.getByRole('button', { name: '刷新环境配置' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Python tests' })).toBeNull());
  expect(screen.queryByRole('button', { name: 'Docker 沙箱' })).toBeNull();
  expect(screen.getByRole('button', { name: '此计算机' })).toBeTruthy();
});
it('keeps a saved profile name even when the configuration no longer contains it', async () => {
  const api = new WebApi('synthetic', '11111111-1111-1111-1111-111111111111');
  vi.spyOn(api, 'executionOptions').mockResolvedValue({ ...options, profiles: [] });
  render(<ExecutionSelector api={api} session={{ execution: { kind: 'sandbox', state: 'unavailable',
    profile_name: 'Original environment' } } as never} disabled={false} create={vi.fn()} refresh={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '执行位置：Original environment' }));
  expect(await screen.findByText(/当前会话使用Original environment/)).toBeTruthy();
});
it('ignores an earlier options response after a newer refresh has completed', async () => {
  let finish!: (value: ExecutionOptions) => void;
  const api = new WebApi('synthetic', '11111111-1111-1111-1111-111111111111');
  vi.spyOn(api, 'executionOptions').mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue({ ...options, profiles: [options.profiles![1]], default_profile: 'node' });
  vi.spyOn(api, 'refreshEnvironments').mockResolvedValue({});
  render(<ExecutionSelector api={api} session={null} disabled={false} create={vi.fn()} refresh={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '执行位置：此计算机' }));
  fireEvent.click(screen.getByRole('button', { name: '刷新环境配置' }));
  await screen.findByRole('button', { name: 'Node tests' });
  await act(async () => finish(options));
  expect(screen.queryByRole('button', { name: 'Python tests' })).toBeNull();
});
it('returns keyboard focus to the environment trigger after confirming a draft', async () => {
  setup();
  await userEvent.click(await screen.findByRole('button', { name: 'Python tests' }));
  await userEvent.click(screen.getByRole('button', { name: '使用此环境' }));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '选择执行位置' })).toBeNull());
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '执行位置：此计算机' }));
});
it('keeps an unconfirmed original request locked when its template is removed', async () => {
  const api = new WebApi('synthetic', '11111111-1111-1111-1111-111111111111');
  vi.spyOn(api, 'executionOptions').mockResolvedValue({ ...options, profiles: [], default_profile: null });
  render(<ExecutionSelector api={api} session={null} disabled={false} locked create={vi.fn()} refresh={vi.fn()}
    draft={{ execution: 'sandbox', include_untracked: [], profile_id: 'python', profile_name: 'Original Python', expected_profile_revision: 'a'.repeat(64) }} />);
  fireEvent.click(screen.getByRole('button', { name: '执行位置：Original Python' }));
  await screen.findByText('没有已配置的沙箱环境。');
  expect(screen.getByText(/再次发送会核对原请求/)).toBeTruthy();
  expect(screen.queryByText(/请重新选择环境/)).toBeNull();
});
