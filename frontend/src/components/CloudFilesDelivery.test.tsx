import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CloudFilesDelivery from './CloudFilesDelivery';
import type { CloudApi } from '../cloudApi';
import type { ReceiveRecord } from '../cloudReceiveTypes';
import { ApiError } from '../api';
afterEach(() => { cleanup(); sessionStorage.clear(); });
function setup() {
  const api = { projectId: 'P', browseDirectories: vi.fn().mockResolvedValue({ path: '/reports', parent: '/', identity: 'seen', separator: '/', items: [], positions: [], next_offset: null, default_parent_dir: '/reports', default_error: null }),
    delivery: vi.fn(), executeDelivery: vi.fn(), recheckDelivery: vi.fn(), deliveryStatus: vi.fn().mockRejectedValue(new ApiError('cloud_delivery_not_found')) } as unknown as CloudApi;
  const lock = vi.fn();
  render(<CloudFilesDelivery api={api} sid="S" resultKey="B.R" active selected={['F']} onSelectionLock={lock}
    prepareMaterials={vi.fn().mockResolvedValue({ receive_id: 'C' } as ReceiveRecord)} />);
  return { api, lock };
}
async function choose() {
  fireEvent.click(screen.getByRole('button', { name: '保存勾选文件到本地' }));
  const button = await screen.findByRole('button', { name: '选择此文件夹' }); await waitFor(() => expect(button).not.toHaveProperty('disabled', true)); fireEvent.click(button);
}
const ready = async (_s: string, value: object) => ({ ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [], conflicts: [], error_code: null });
it('unlocks the destination after definitive rejection before a save was registered', async () => {
  const { api, lock } = setup(); vi.mocked(api.delivery).mockRejectedValueOnce(new ApiError('invalid_request')).mockImplementation(ready as never);
  await choose(); await waitFor(() => expect(api.delivery).toHaveBeenCalledOnce());
  expect(await screen.findByRole('button', { name: '保存勾选文件到本地' })).not.toHaveProperty('disabled', true);
  expect(lock).toHaveBeenLastCalledWith(false); await choose(); await screen.findByText('保存预检已完成');
  expect(vi.mocked(api.delivery).mock.calls[1][1].command_id).not.toBe(vi.mocked(api.delivery).mock.calls[0][1].command_id);
});
it('keeps the fixed destination and command after a lost acknowledgement', async () => {
  const { api } = setup(); vi.mocked(api.delivery).mockRejectedValueOnce(new TypeError('lost ACK')).mockImplementation(ready as never);
  await choose(); const retry = await screen.findByRole('button', { name: '核对原保存请求' }); await waitFor(() => expect(retry).not.toHaveProperty('disabled', true));
  fireEvent.click(retry); await screen.findByText('保存预检已完成');
  expect(vi.mocked(api.delivery).mock.calls[1]).toEqual(vi.mocked(api.delivery).mock.calls[0]);
});
it('saves through the save button after preflight without a second acknowledgement checkbox', async () => {
  const { api } = setup(); vi.mocked(api.delivery).mockImplementation(ready as never);
  await choose(); await screen.findByText('保存预检已完成');
  expect(screen.queryByRole('checkbox', { name: '已核对保存位置和文件' })).toBeNull();
  expect(api.executeDelivery).not.toHaveBeenCalled();
  const request = vi.mocked(api.delivery).mock.calls[0][1];
  vi.mocked(api.executeDelivery).mockResolvedValue({ ...await ready('S', request), phase: 'completed' } as never);
  const save = screen.getByRole('button', { name: '确认保存文件' }); expect(save).not.toHaveProperty('disabled', true);
  fireEvent.click(save); await screen.findByText('文件已保存到所选文件夹');
  expect(api.executeDelivery).toHaveBeenCalledExactlyOnceWith('S', request.command_id, false);
});
it('requires overwrite permission for different content and clears it after rechecking', async () => {
  const { api } = setup(); vi.mocked(api.delivery).mockImplementation(async (sid, value) => ({ ...await ready(sid, value), conflicts: ['report.txt'], force_allowed: true }) as never);
  await choose(); await screen.findByText('保存预检已完成');
  const request = vi.mocked(api.delivery).mock.calls[0][1];
  const record = { ...await ready('S', request), conflicts: ['report.txt'], force_allowed: true };
  vi.mocked(api.recheckDelivery).mockResolvedValue(record as never);
  vi.mocked(api.executeDelivery).mockResolvedValue({ ...record, phase: 'completed' } as never);
  const save = screen.getByRole('button', { name: '确认保存文件' });
  expect(save).toHaveProperty('disabled', true); expect(api.executeDelivery).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox', { name: '确认覆盖以上同名文件' }));
  expect(save).not.toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '重新预检保存' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '重新预检保存' })).not.toHaveProperty('disabled', true));
  expect(save).toHaveProperty('disabled', true);
  expect(screen.getByRole('checkbox', { name: '确认覆盖以上同名文件' })).toHaveProperty('checked', false);
  fireEvent.click(screen.getByRole('checkbox', { name: '确认覆盖以上同名文件' })); fireEvent.click(save);
  await screen.findByText('文件已保存到所选文件夹');
  expect(api.executeDelivery).toHaveBeenCalledExactlyOnceWith('S', request.command_id, true);
});
it('queries a lost save acknowledgement before explicitly continuing the same operation', async () => {
  const { api } = setup(); vi.mocked(api.delivery).mockImplementation(ready as never);
  await choose(); await screen.findByText('保存预检已完成');
  const request = vi.mocked(api.delivery).mock.calls[0][1]; const record = await ready('S', request);
  vi.mocked(api.executeDelivery).mockRejectedValueOnce(new TypeError('lost ACK')).mockResolvedValue({ ...record, phase: 'completed' } as never);
  fireEvent.click(screen.getByRole('button', { name: '确认保存文件' }));
  const query = screen.getByRole('button', { name: '查询保存状态' });
  await waitFor(() => expect(query).not.toHaveProperty('disabled', true));
  expect(screen.getByText('原保存结果未确认，请查询保存状态后继续。')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '确认保存文件' })).toBeNull(); expect(api.executeDelivery).toHaveBeenCalledOnce();
  vi.mocked(api.deliveryStatus).mockResolvedValue({ ...record, phase: 'partial' } as never);
  fireEvent.click(query); await screen.findByText('保存尚未完成，请核对原进度');
  expect(api.executeDelivery).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: '确认保存文件' })); await screen.findByText('文件已保存到所选文件夹');
  expect(vi.mocked(api.executeDelivery).mock.calls).toEqual([['S', request.command_id, false], ['S', request.command_id, false]]);
});
