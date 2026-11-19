import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CloudResultReceive from './CloudResultReceive';
import type { CloudApi } from '../cloudApi';
import { useCloudReceive } from '../useCloudReceive';
import { ApiError } from '../api';
afterEach(() => { cleanup(); sessionStorage.clear(); });
const files = [{ role: 'artifact' as const, artifact: { artifact_id: 'F', relative_name: 'report.json', size: 10, sha256: 'a'.repeat(64) } }];
const record = (id: string, phase = 'available') => ({ receive_id: id, command_id: id, local_project_id: 'P', local_session_id: 'S',
  binding_id: 'B', result_id: 'R', input_id: 'I', phase, active: false, error_code: null, files: [] });
function client() { return { projectId: 'P', receive: vi.fn(), receiveStatus: vi.fn().mockRejectedValue(new ApiError('cloud_receive_not_found')),
  retryReceive: vi.fn() } as unknown as CloudApi; }
it('retries an unconfirmed receive using its original ID and frozen selection', async () => {
  const api = client(); vi.mocked(api.receive).mockRejectedValueOnce(new TypeError('lost ACK')).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  const { result } = renderHook(() => useCloudReceive(api, 'S', 'B', 'R', 'I'));
  await act(async () => { await expect(result.current.ensure(true, ['F'])).rejects.toThrow('lost ACK'); });
  await act(async () => { await result.current.ensure(true, ['F']); });
  expect(vi.mocked(api.receive).mock.calls[1]).toEqual(vi.mocked(api.receive).mock.calls[0]);
  expect(result.current.record?.phase).toBe('available');
});
it('explicitly resumes failed material preparation and reuses verified cached receipts', async () => {
  const api = client(); vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  const { result } = renderHook(() => useCloudReceive(api, 'S', 'B', 'R', 'I'));
  await act(async () => { await result.current.ensure(false, ['F']); });
  const id = result.current.record!.receive_id;
  vi.mocked(api.receiveStatus).mockResolvedValue(record(id, 'failed') as never); vi.mocked(api.retryReceive).mockResolvedValue(record(id) as never);
  await act(async () => { await result.current.ensure(false, ['F']); });
  expect(api.retryReceive).toHaveBeenCalledWith('S', id); expect(api.receive).toHaveBeenCalledOnce();
  vi.mocked(api.receiveStatus).mockResolvedValue(record(id) as never);
  await act(async () => { await result.current.ensure(false, ['F']); });
  expect(api.receive).toHaveBeenCalledOnce(); expect(api.retryReceive).toHaveBeenCalledOnce();
});
it('can explicitly refresh original material after local cache loss', async () => {
  const api = client(); vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  const { result } = renderHook(() => useCloudReceive(api, 'S', 'B', 'R', 'I'));
  await act(async () => { await result.current.ensure(true, ['F']); });
  const id = result.current.record!.receive_id;
  vi.mocked(api.receiveStatus).mockResolvedValue(record(id) as never); vi.mocked(api.retryReceive).mockResolvedValue(record(id) as never);
  await act(async () => { await result.current.ensure(true, ['F'], true); });
  expect(api.retryReceive).toHaveBeenCalledWith('S', id, true); expect(api.receive).toHaveBeenCalledOnce();
  expect(result.current.record!.receive_id).toBe(id);
});
it('locks report selection to the exact save intent until explicitly changing that intent', async () => {
  const api = client(); vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  Object.assign(api, { browseDirectories: vi.fn().mockResolvedValue({ path: '/reports', identity: 'seen', parent: '/', separator: '/', positions: [], items: [], next_offset: null }),
    delivery: vi.fn(async (_s, value) => ({ ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [{ path: 'report.json', operation: 'modify' }], conflicts: ['report.json'], force_allowed: true, error_code: null })) });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  const checkbox = screen.getByRole('checkbox', { name: /report.json/ }); fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole('button', { name: '选择文件夹' })); const select = await screen.findByRole('button', { name: '选择此文件夹' });
  await waitFor(() => expect(select).toHaveProperty('disabled', false));
  fireEvent.click(select);
  expect(checkbox).not.toHaveProperty('disabled', true); expect(api.receive).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('button', { name: '保存到本地' })).toHaveProperty('disabled', false));
  fireEvent.click(screen.getByRole('button', { name: '保存到本地' }));
  await screen.findByText('保存预检已完成'); expect(checkbox).toHaveProperty('disabled', true);
  expect(api.receive).toHaveBeenCalledWith('S', 'B', 'R', expect.objectContaining({ artifact_ids: ['F'], include_code: false }));
  fireEvent.click(screen.getByRole('button', { name: '更换保存位置或选择' })); await waitFor(() => expect(checkbox).toHaveProperty('disabled', false));
});
it('restores report selection when the result window is reopened', () => {
  const api = client(); const props = { api, sid: 'S', bid: 'B', rid: 'R', inputId: 'I', files, active: true };
  const first = render(<CloudResultReceive {...props} />); fireEvent.click(screen.getByRole('checkbox', { name: /report.json/ })); first.unmount();
  render(<CloudResultReceive {...props} />); expect(screen.getByRole('checkbox', { name: /report.json/ })).toHaveProperty('checked', true);
});
it('cleans only on explicit request and reports actual freed cache bytes', async () => {
  const api = client(); const cleanupReceiveCache = vi.fn().mockResolvedValue({ removed_files: 2, freed_bytes: 17 });
  Object.assign(api, { cleanupReceiveCache });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  expect(cleanupReceiveCache).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '清理未引用缓存' }));
  expect(await screen.findByText('已清理 2 个缓存文件，释放 17 字节。')).toBeTruthy();
  expect(cleanupReceiveCache).toHaveBeenCalledWith('S');
  expect(api.receive).not.toHaveBeenCalled();
});
