import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CloudFilesDelivery from './CloudFilesDelivery';
import type { CloudApi } from '../cloudApi';
import type { ReceiveRecord } from '../cloudReceiveTypes';
import type { DeliveryRecord } from '../cloudDeliveryTypes';
import { ApiError } from '../api';
afterEach(() => { cleanup(); sessionStorage.clear(); });
function setup() {
  const api = { projectId: 'P', browseDirectories: vi.fn().mockResolvedValue({ path: '/reports', parent: '/', identity: 'seen', separator: '/', items: [], positions: [], next_offset: null, default_parent_dir: '/reports', default_error: null }),
    delivery: vi.fn(), executeDelivery: vi.fn(), recheckDelivery: vi.fn(), deliveryStatus: vi.fn().mockRejectedValue(new ApiError('cloud_delivery_not_found')) } as unknown as CloudApi;
  const lock = vi.fn();
  return { api, lock, view: mount(api, lock) };
}
function mount(api: CloudApi, lock: (locked: boolean) => void) {
  return render(<CloudFilesDelivery api={api} sid="S" resultKey="B.R" active selected={['F']} onSelectionLock={lock}
    prepareMaterials={vi.fn().mockResolvedValue({ receive_id: 'C' } as ReceiveRecord)} />);
}
async function choose() {
  fireEvent.click(screen.getByRole('button', { name: '选择文件夹' }));
  const button = await screen.findByRole('button', { name: '选择此文件夹' }); await waitFor(() => expect(button).not.toHaveProperty('disabled', true)); fireEvent.click(button);
}
async function save() {
  await choose();
  fireEvent.click(screen.getByRole('button', { name: '保存到本地' }));
}
type SaveRequest = Parameters<CloudApi['delivery']>[1];
const ready = async (_s: string, value: SaveRequest): Promise<DeliveryRecord> =>
  ({ ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [], conflicts: [], error_code: null, branch_head: null });
it('unlocks the destination after definitive rejection before a save was registered', async () => {
  const { api, lock } = setup(); vi.mocked(api.delivery).mockRejectedValueOnce(new ApiError('invalid_request')).mockImplementation(ready as never);
  await save(); await waitFor(() => expect(api.delivery).toHaveBeenCalledOnce());
  await waitFor(() => expect(screen.getByRole('button', { name: '保存到本地' })).not.toHaveProperty('disabled', true));
  expect(lock).toHaveBeenLastCalledWith(false);
  fireEvent.click(screen.getByRole('button', { name: '保存到本地' }));
  await waitFor(() => expect(api.delivery).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.delivery).mock.calls[1][1].command_id).not.toBe(vi.mocked(api.delivery).mock.calls[0][1].command_id);
});
it('keeps the fixed destination and command after a lost acknowledgement', async () => {
  const { api } = setup(); vi.mocked(api.delivery).mockRejectedValueOnce(new TypeError('lost ACK')).mockImplementation(ready as never);
  await save(); const retry = await screen.findByRole('button', { name: '核对原保存请求' }); await waitFor(() => expect(retry).not.toHaveProperty('disabled', true));
  fireEvent.click(retry); await screen.findByText('保存预检已完成');
  expect(vi.mocked(api.delivery).mock.calls[1]).toEqual(vi.mocked(api.delivery).mock.calls[0]);
  expect(api.executeDelivery).not.toHaveBeenCalled();
});
it('selects the directory without writing, then saves after asynchronous preflight from one click', async () => {
  const { api } = setup(); let fixed: SaveRequest;
  vi.mocked(api.delivery).mockImplementation(async (sid, request) => { fixed = request; return { ...await ready(sid, request), phase: 'preparing' }; });
  vi.mocked(api.deliveryStatus).mockImplementation(sid => ready(sid, fixed));
  vi.mocked(api.executeDelivery).mockImplementation(async sid => ({ ...await ready(sid, fixed), phase: 'completed' }));
  await choose();
  expect(api.delivery).not.toHaveBeenCalled();
  expect(api.executeDelivery).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '保存到本地' }));
  await screen.findByText('文件已保存到所选文件夹');
  const request = vi.mocked(api.delivery).mock.calls[0][1];
  expect(api.executeDelivery).toHaveBeenCalledExactlyOnceWith('S', request.command_id, false);
});
it('requires overwrite permission for different content and clears it after rechecking', async () => {
  const { api } = setup(); vi.mocked(api.delivery).mockImplementation(async (sid, value) => ({ ...await ready(sid, value), conflicts: ['report.txt'], force_allowed: true }) as never);
  await save(); await screen.findByText('保存预检已完成');
  const request = vi.mocked(api.delivery).mock.calls[0][1];
  const record = { ...await ready('S', request), conflicts: ['report.txt'], force_allowed: true };
  vi.mocked(api.recheckDelivery).mockResolvedValue(record as never);
  vi.mocked(api.executeDelivery).mockResolvedValue({ ...record, phase: 'completed' } as never);
  expect(screen.getByText('report.txt')).toBeTruthy(); expect(api.executeDelivery).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '重新预检保存' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '重新预检保存' })).not.toHaveProperty('disabled', true));
  expect(api.executeDelivery).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认覆盖并保存' }));
  await screen.findByText('文件已保存到所选文件夹');
  expect(api.executeDelivery).toHaveBeenCalledExactlyOnceWith('S', request.command_id, true);
});
it('queries a lost save acknowledgement before explicitly continuing the same operation', async () => {
  const { api } = setup(); vi.mocked(api.delivery).mockImplementation(ready as never);
  vi.mocked(api.executeDelivery).mockRejectedValueOnce(new TypeError('lost ACK'));
  await save();
  const query = await screen.findByRole('button', { name: '查询保存状态' });
  const request = vi.mocked(api.delivery).mock.calls[0][1]; const record = await ready('S', request);
  vi.mocked(api.executeDelivery).mockResolvedValue({ ...record, phase: 'completed' } as never);
  await waitFor(() => expect(query).not.toHaveProperty('disabled', true));
  expect(screen.getByText('原保存结果未确认，请查询保存状态后继续。')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '继续原保存' })).toBeNull(); expect(api.executeDelivery).toHaveBeenCalledOnce();
  vi.mocked(api.deliveryStatus).mockResolvedValue({ ...record, phase: 'partial' } as never);
  fireEvent.click(query); await screen.findByText('保存尚未完成，请核对原进度');
  expect(api.executeDelivery).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: '继续原保存' })); await screen.findByText('文件已保存到所选文件夹');
  expect(vi.mocked(api.executeDelivery).mock.calls).toEqual([['S', request.command_id, false], ['S', request.command_id, false]]);
});
it('reopens a pending save by querying the same command without executing again', async () => {
  const { api, view } = setup();
  vi.mocked(api.delivery).mockImplementation(ready as never);
  vi.mocked(api.executeDelivery).mockRejectedValue(new TypeError('lost ACK'));
  await save();
  await screen.findByText('原保存结果未确认，请查询保存状态后继续。');
  const fixed = vi.mocked(api.delivery).mock.calls[0][1];
  view.unmount();
  vi.mocked(api.deliveryStatus).mockResolvedValue({ ...await ready('S', fixed), phase: 'completed' } as never);
  mount(api, vi.fn());
  await screen.findByText('文件已保存到所选文件夹');
  expect(api.deliveryStatus).toHaveBeenLastCalledWith('S', fixed.command_id, expect.any(AbortSignal));
  expect(api.executeDelivery).toHaveBeenCalledExactlyOnceWith('S', fixed.command_id, false);
});
it('allows a manual status query after losing the preflight polling response', async () => {
  const { api } = setup(); let fixed: SaveRequest;
  vi.mocked(api.delivery).mockImplementation(async (sid, request) => { fixed = request; return { ...await ready(sid, request), phase: 'preparing' }; });
  vi.mocked(api.deliveryStatus).mockRejectedValueOnce(new TypeError('lost status')).mockImplementation(sid => ready(sid, fixed));
  await save();
  await screen.findByRole('alert');
  const query = screen.getByRole('button', { name: '查询保存状态' });
  expect(query).not.toHaveProperty('disabled', true);
  fireEvent.click(query);
  await screen.findByText('保存预检已完成');
  expect(api.executeDelivery).not.toHaveBeenCalled();
});
