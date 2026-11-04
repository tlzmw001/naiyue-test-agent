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
  retryReceive: vi.fn(), analysisDraft: vi.fn() } as unknown as CloudApi; }
it('retries an unconfirmed receive using its original ID and frozen selection', async () => {
  const api = client(); vi.mocked(api.receive).mockRejectedValueOnce(new TypeError('lost ACK')).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  const { result } = renderHook(() => useCloudReceive(api, 'S', 'B', 'R', 'I'));
  await act(async () => { await expect(result.current.ensure(true, ['F'])).rejects.toThrow('lost ACK'); });
  await act(async () => { await result.current.ensure(true, ['F']); });
  expect(vi.mocked(api.receive).mock.calls[1]).toEqual(vi.mocked(api.receive).mock.calls[0]);
  expect(result.current.record?.phase).toBe('available'); expect(api.analysisDraft).not.toHaveBeenCalled();
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
it('prepares code only on preflight and keeps a separate confirmation before writing', async () => {
  const api = client(); vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  let fixed: object; const delivery = vi.fn(async (_s, value) => { fixed = value; return { ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [{ path: 'a.py', operation: 'modify' }], conflicts: [], error_code: null }; });
  const executeDelivery = vi.fn(async () => ({ ...fixed!, local_project_id: 'P', local_session_id: 'S', phase: 'completed', changes: [], conflicts: [], error_code: null }));
  Object.assign(api, { delivery, executeDelivery });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  expect(api.receive).not.toHaveBeenCalled(); expect(screen.queryByRole('option', { name: /导出/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  expect(await screen.findByText('修改 · a.py')).toBeTruthy(); expect(executeDelivery).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '确认执行交付' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('checkbox', { name: '我已核对目标和变化，确认执行此交付' }));
  fireEvent.click(screen.getByRole('button', { name: '确认执行交付' }));
  expect(await screen.findByText('代码交付已完成')).toBeTruthy();
  expect(api.receive).toHaveBeenCalledWith('S', 'B', 'R', expect.objectContaining({ include_code: true, artifact_ids: [] }));
  expect(delivery).toHaveBeenCalledWith('S', expect.objectContaining({ action: 'apply', destination_handle: 'P', name: '' }));
});
it('requires explicit force selection for content conflicts', async () => {
  const api = client(); vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  const delivery = vi.fn(async (_s, value) => ({ ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [], conflicts: ['changed.py'], force_allowed: true, error_code: null }));
  const executeDelivery = vi.fn(async (_s, id) => ({ command_id: id, receive_id: vi.mocked(api.receive).mock.calls[0][3].command_id, local_project_id: 'P', local_session_id: 'S', action: 'apply', name: '', phase: 'completed', changes: [], conflicts: [], error_code: null }));
  Object.assign(api, { delivery, executeDelivery });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' })); await screen.findByText('changed.py');
  fireEvent.click(screen.getByRole('checkbox', { name: '我已核对目标和变化，确认执行此交付' }));
  expect(screen.getByRole('button', { name: '确认执行交付' })).toHaveProperty('disabled', true); expect(executeDelivery).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox', { name: /强制应用/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: '我已核对目标和变化，确认执行此交付' }));
  fireEvent.click(screen.getByRole('button', { name: '确认执行交付' }));
  await waitFor(() => expect(executeDelivery).toHaveBeenCalledWith('S', expect.any(String), true));
});
it('shows material progress and defers analysis until fixed bytes are available', async () => {
  const api = client(); let finish!: (value: never) => void;
  vi.mocked(api.receive).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const accept = vi.fn(); render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active onAnalysisDraft={accept} />);
  fireEvent.click(screen.getByRole('button', { name: '交给主 Agent 分析' }));
  expect(await screen.findByText('正在准备并核对固定材料')).toBeTruthy(); expect(api.analysisDraft).not.toHaveBeenCalled();
  expect(accept).not.toHaveBeenCalled(); await act(async () => finish(record('wrong') as never));
  expect(await screen.findByRole('alert')).toBeTruthy(); expect(api.analysisDraft).not.toHaveBeenCalled();
});
it('repairs the fixed original code material through a visible refresh action', async () => {
  const api = client(); let id = '';
  vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => { id = body.command_id; return record(id) as never; });
  vi.mocked(api.receiveStatus).mockImplementation(async () => record(id) as never); vi.mocked(api.retryReceive).mockImplementation(async () => record(id) as never);
  Object.assign(api, { delivery: vi.fn(async (_s, value) => ({ ...value, local_project_id: 'P', local_session_id: 'S', phase: 'failed', changes: [], conflicts: [], error_code: 'cloud_material_unavailable' })) });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  fireEvent.click(await screen.findByRole('button', { name: '重新核对代码材料' }));
  await waitFor(() => expect(api.retryReceive).toHaveBeenCalledWith('S', id, true)); expect(api.receive).toHaveBeenCalledOnce();
});
it('locks report selection to the exact save intent until explicitly changing that intent', async () => {
  const api = client(); vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  Object.assign(api, { browseDirectories: vi.fn().mockResolvedValue({ path: '/reports', identity: 'seen', parent: '/', separator: '/', positions: [], items: [], next_offset: null }),
    delivery: vi.fn(async (_s, value) => ({ ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [{ path: 'report.json', operation: 'add' }], conflicts: [], error_code: null })) });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  const checkbox = screen.getByRole('checkbox', { name: /report.json/ }); fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole('button', { name: '保存勾选文件到本地' })); fireEvent.click(await screen.findByRole('button', { name: '选择此文件夹' }));
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
  expect(api.receive).not.toHaveBeenCalled(); expect(api.analysisDraft).not.toHaveBeenCalled();
});
