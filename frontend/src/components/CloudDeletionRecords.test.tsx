import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { CloudApi } from '../cloudApi';
import type { CloudOperation } from '../cloudTypes';
import CloudDeletionRecords from './CloudDeletionRecords';

afterEach(cleanup);

it('preserves task deletion success while showing and retrying a failed input cleanup', async () => {
  const row: CloudOperation = {
    command_id: 'D', binding_id: 'B', task_id: 'task0001', kind: 'delete', phase: 'succeeded', steps: {},
    input_cleanup: { state: 'failed', released_quota_bytes: 0, bytes_complete: true,
      candidates: [{ input_id: 'a'.repeat(64), state: 'failed', reason: { code: 'input_cleanup_io_failed', errno: 13 } }] },
  };
  const api = {
    deletions: vi.fn().mockResolvedValue([row]), operation: vi.fn().mockResolvedValue(row),
    retryInputCleanup: vi.fn().mockResolvedValue({ ...row, input_cleanup: {
      ...row.input_cleanup, state: 'completed', released_quota_bytes: 53,
      candidates: [{ input_id: 'a'.repeat(64), state: 'reclaimed' }],
    } }),
  };
  render(<CloudDeletionRecords api={api as unknown as CloudApi} sid="S" close={vi.fn()} />);
  expect(await screen.findByText('任务 task0001 · 已删除')).toBeTruthy();
  expect(screen.getByText(/系统错误 13/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '核对结果' }));
  await waitFor(() => expect(api.operation).toHaveBeenCalledWith('S', 'D'));
  expect(api.retryInputCleanup).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', { name: '重试输入回收' })).toHaveProperty('disabled', false));
  fireEvent.click(screen.getByRole('button', { name: '重试输入回收' }));
  expect(await screen.findByText(/已释放额度 53 字节/)).toBeTruthy();
  expect(api.retryInputCleanup).toHaveBeenCalledWith('S', 'D');
});

it('shows failure to verify a cached success and can retry pending attachment release alone', async () => {
  const row: CloudOperation = {
    command_id: 'D', binding_id: 'B', task_id: 'task0001', kind: 'delete', phase: 'succeeded',
    steps: {}, attachment_release_pending: true,
  };
  const api = {
    deletions: vi.fn().mockResolvedValue([row]),
    operation: vi.fn().mockRejectedValue({ code: 'cloud_connection_failed' }),
    retryInputCleanup: vi.fn().mockResolvedValue({ ...row, attachment_release_pending: false }),
  };
  render(<CloudDeletionRecords api={api as unknown as CloudApi} sid="S" close={vi.fn()} />);
  const check = await screen.findByRole('button', { name: '核对结果' });
  fireEvent.click(check);
  expect((await screen.findByRole('alert')).textContent).toContain('未能核对最新结果');
  expect(screen.getByText('任务 task0001 · 已删除')).toBeTruthy();
  expect(screen.getByText('还有附件占用待释放。')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '重试输入回收' }));
  await waitFor(() => expect(screen.queryByText('还有附件占用待释放。')).toBeNull());
  expect(api.retryInputCleanup).toHaveBeenCalledWith('S', 'D');
});
