import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import CloudTaskActions from './CloudTaskActions';
import CloudTasks from './CloudTasks';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding } from '../cloudTypes';
import { ApiError } from '../api';
import { installDialogMock } from '../testDialogs';

let restore: () => void;
beforeAll(() => { restore = installDialogMock(); });
afterAll(() => restore());
afterEach(() => cleanup());
const project = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const binding: CloudBinding = { binding_id: 'B', local_session_id: 'S', profile_id: 'old', instance_id: 'I', task_id: 'a'.repeat(32),
  revision: 4, retained: true, preparation: 'prepared', create_command_id: 'C', input_id: null,
  cloud: { task_id: 'a'.repeat(32), project_id: project, input_id: null, revision: 1, sandbox_id: null,
    last_run_id: null, last_error_code: null, environment_operation_id: null, control_operation_id: null,
    artifact_operation_id: null, runner_model_ids: [], lifecycle: 'ready', runner_phase: 'disconnected', environment_phase: 'unknown',
    reclaim_blockers: [], last_quiescent_at: null, active_run_id: null, generation: 1 } };

it('cancels removal and returns focus to its own button without issuing a request', () => {
  const forgetBinding = vi.fn();
  const api = { projectId: project, forgetBinding } as unknown as CloudApi;
  render(<CloudTaskActions api={api} sid="S" binding={binding} busy execute={vi.fn()} />);
  const button = screen.getByRole('button', { name: '移除本机关联' });
  fireEvent.click(button);
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(forgetBinding).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(button);
});
it('offers local removal next to delete even when metadata is stale, with explicit confirmation', async () => {
  const removed = vi.fn();
  const forgetBinding = vi.fn().mockResolvedValue({ ...binding, retained: false });
  const api = { projectId: project, forgetBinding } as unknown as CloudApi;
  render(<CloudTaskActions api={api} sid="S" binding={binding} busy execute={vi.fn()} onForgotten={removed} />);
  const button = screen.getByRole('button', { name: '移除本机关联' });
  expect(button.previousElementSibling).toBe(screen.getByRole('button', { name: '删除任务' }));
  expect(button).not.toHaveProperty('disabled', true);
  fireEvent.click(button);
  expect(screen.getByRole('dialog', { name: '移除此任务的本机关联？' })).toBeTruthy();
  expect(forgetBinding).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await waitFor(() => expect(removed).toHaveBeenCalledOnce());
  expect(forgetBinding).toHaveBeenCalledExactlyOnceWith('S', 'B', {
    command_id: expect.any(String), expected_binding_revision: 4, acknowledge_unmanaged: true,
  });
});

it('keeps the confirmation and original request after an unknown response, then permits retry', async () => {
  const removed = vi.fn();
  const forgetBinding = vi.fn().mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValue({ ...binding, retained: false });
  const api = { projectId: project, forgetBinding } as unknown as CloudApi;
  render(<CloudTaskActions api={api} sid="S" binding={binding} busy execute={vi.fn()} onForgotten={removed} />);
  fireEvent.click(screen.getByRole('button', { name: '移除本机关联' }));
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await screen.findByRole('alert');
  expect(removed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await waitFor(() => expect(removed).toHaveBeenCalledOnce());
  expect(forgetBinding.mock.calls[1]).toEqual(forgetBinding.mock.calls[0]);
});

it('removes the selected unavailable binding immediately and ignores a stale list response', async () => {
  const profiles = vi.fn().mockResolvedValue([]);
  const lost = { ...binding, cloud: null };
  let late!: (value: CloudBinding[]) => void;
  const api = { projectId: project, profiles, list: vi.fn().mockResolvedValue([lost]), binding: vi.fn().mockRejectedValue(new ApiError('cloud_profile_not_found')),
    forgetBinding: vi.fn().mockResolvedValue({ ...lost, retained: false }), retry: vi.fn() } as unknown as CloudApi;
  render(<CloudTasks api={api} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  expect(api.binding).toHaveBeenCalledOnce();
  expect(screen.getByText(/找不到配置的云端连接/)).toBeTruthy();
  vi.mocked(api.list).mockImplementation(() => new Promise(resolve => { late = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '刷新' }));
  await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button', { name: '移除本机关联' }));
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: /任务 aaaaaaaa/ })).toBeNull());
  await act(async () => late([lost]));
  expect(api.binding).toHaveBeenCalledOnce();
  expect(screen.queryByRole('button', { name: /任务 aaaaaaaa/ })).toBeNull();
  expect(screen.getByText('还没有云端任务')).toBeTruthy();
});

it('keeps the removal entry in cached task details while stopping requests to a missing profile', async () => {
  const api = { projectId: project, profiles: vi.fn().mockResolvedValue([]), list: vi.fn().mockResolvedValue([binding]),
    binding: vi.fn().mockRejectedValue(new ApiError('cloud_profile_not_found')), snapshot: vi.fn(), operations: vi.fn(), models: vi.fn(),
    draft: vi.fn().mockResolvedValue({ text: '', model_id: null, draft_revision: 0, attachment_ids: [] }),
    forgetBinding: vi.fn().mockResolvedValue({ ...binding, retained: false }) } as unknown as CloudApi;
  render(<CloudTasks api={api} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  const remove = await screen.findByRole('button', { name: '移除本机关联' });
  expect(remove).not.toHaveProperty('disabled', true);
  expect(screen.getByRole('button', { name: '删除任务' })).toHaveProperty('disabled', true);
  expect(api.binding).toHaveBeenCalledOnce();
  expect(api.snapshot).not.toHaveBeenCalled();
  expect(api.operations).not.toHaveBeenCalled();
  expect(api.models).not.toHaveBeenCalled();
  fireEvent.click(remove);
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await screen.findByText('还没有云端任务');
});

it('retries a confirmed missing profile only after its configuration returns', async () => {
  const lost = { ...binding, cloud: null };
  const api = { projectId: project, profiles: vi.fn().mockResolvedValue([]), list: vi.fn().mockResolvedValue([lost]),
    binding: vi.fn().mockRejectedValueOnce(new ApiError('cloud_profile_not_found')).mockResolvedValue(lost),
  } as unknown as CloudApi;
  render(<CloudTasks api={api} sessionId="S" active />);
  await screen.findByText(/找不到配置的云端连接/);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '刷新' })));
  expect(api.list).toHaveBeenCalledTimes(2);
  expect(api.binding).toHaveBeenCalledOnce();
  vi.mocked(api.profiles).mockResolvedValue([{ profile_id: 'old', template_ids: [], default_template: '', connection: '', tunnel: '' }]);
  fireEvent.click(screen.getByRole('button', { name: '刷新' }));
  await waitFor(() => expect(api.binding).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText(/找不到配置的云端连接/)).toBeNull());
});
