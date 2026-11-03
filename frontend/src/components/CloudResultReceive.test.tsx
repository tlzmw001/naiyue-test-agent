import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CloudResultReceive from './CloudResultReceive';
import type { CloudApi } from '../cloudApi';

afterEach(() => { cleanup(); sessionStorage.clear(); });
const files = [{ role: 'artifact' as const, artifact: { artifact_id: 'F', relative_name: 'report.json', size: 10, sha256: 'a'.repeat(64) } }];
const record = (id: string, phase = 'available') => ({ receive_id: id, command_id: id, local_project_id: 'P', local_session_id: 'S',
  binding_id: 'B', result_id: 'R', input_id: 'I', phase, active: false, error_code: null, files: [] });
function client() {
  return { projectId: 'P', receive: vi.fn(), receiveStatus: vi.fn().mockRejectedValue(new Error('missing original receipt')),
    retryReceive: vi.fn(), analysisDraft: vi.fn() } as unknown as CloudApi;
}
it('retries an unconfirmed initial receive with its original ID and frozen selection', async () => {
  const api = client();
  vi.mocked(api.receive).mockRejectedValueOnce(new TypeError('lost ACK')).mockImplementationOnce(async (_s, _b, _r, body) => record(body.command_id) as never);
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  fireEvent.click(screen.getByRole('checkbox', { name: /report.json/ }));
  fireEvent.click(screen.getByRole('button', { name: '接收到本机' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '接收到本机' })).toHaveProperty('disabled', false));
  expect(screen.getByRole('checkbox', { name: /report.json/ })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '接收到本机' }));
  expect(await screen.findByText('本机材料已核对')).toBeTruthy();
  expect(vi.mocked(api.receive).mock.calls[1]).toEqual(vi.mocked(api.receive).mock.calls[0]);
  expect(api.analysisDraft).not.toHaveBeenCalled();
});
it('restores local failed status without starting a download and explicitly retries the same receive', async () => {
  sessionStorage.setItem('naiyue.cloud-receive.P.S.B.R', JSON.stringify({ command_id: 'C', artifact_ids: ['F'] }));
  const api = client(); vi.mocked(api.receiveStatus).mockResolvedValue(record('C', 'failed') as never);
  vi.mocked(api.retryReceive).mockResolvedValue(record('C') as never);
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  expect(await screen.findByText('本机接收失败')).toBeTruthy(); expect(api.receive).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '继续原接收' }));
  expect(await screen.findByText('本机材料已核对')).toBeTruthy(); expect(api.retryReceive).toHaveBeenCalledWith('S', 'C');
});

it('creates a new explicit receive after available so missing local material can be downloaded again', async () => {
  const api = client();
  vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  fireEvent.click(screen.getByRole('checkbox', { name: /report.json/ }));
  fireEvent.click(screen.getByRole('button', { name: '接收到本机' }));
  await screen.findByText('本机材料已核对');
  fireEvent.click(screen.getByRole('button', { name: '接收到本机' }));
  await waitFor(() => expect(api.receive).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.receive).mock.calls[1][3].command_id).not.toBe(vi.mocked(api.receive).mock.calls[0][3].command_id);
  expect(vi.mocked(api.receive).mock.calls[1][3].artifact_ids).toEqual(['F']);
  expect(api.retryReceive).not.toHaveBeenCalled();
});
it('shows actual in-progress bytes and does not offer analysis until available', async () => {
  const api = client();
  vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => ({ ...record(body.command_id, 'downloading'), active: true,
    files: [{ material_id: 'M', role: 'artifact', artifact: files[0].artifact, offset: 4, verified: false }] }) as never);
  vi.mocked(api.receiveStatus).mockImplementation(async (_s, id) => ({ ...record(id, 'verifying'), active: true, files: [] }) as never);
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active onAnalysisDraft={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '接收到本机' }));
  expect(await screen.findByText(/正在核对本机材料/)).toBeTruthy();
  expect(screen.queryByRole('button', { name: '交给主会话分析' })).toBeNull(); expect(api.analysisDraft).not.toHaveBeenCalled();
});

it('previews export changes and requires a separate confirmation before writing project files', async () => {
  const api = client(); let lastRequest: { command_id: string; receive_id: string; name: string };
  vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  const delivery = vi.fn(async (_s, value) => { lastRequest = value; return { ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [{ path: 'a.py', operation: 'modify' }], conflicts: [], error_code: null, branch_head: null }; });
  const executeDelivery = vi.fn(async () => ({ ...lastRequest!, action: 'export', local_project_id: 'P', local_session_id: 'S', phase: 'completed', changes: [{ path: 'a.py', operation: 'modify' }], conflicts: [], error_code: null, branch_head: null }));
  Object.assign(api, { delivery, executeDelivery });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  expect(screen.queryByRole('combobox', { name: '代码交付方式' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '接收到本机' }));
  fireEvent.change(await screen.findByRole('textbox', { name: '新目录名' }), { target: { value: 'result-copy' } });
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  expect(await screen.findByText('修改 · a.py')).toBeTruthy();
  expect(executeDelivery).not.toHaveBeenCalled();
  const confirm = screen.getByRole('button', { name: '确认执行交付' }); expect(confirm).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('checkbox', { name: '我已核对目标和变化，确认执行此交付' })); fireEvent.click(confirm);
  expect(await screen.findByText('代码交付已完成')).toBeTruthy();
  expect(delivery).toHaveBeenCalledWith('S', expect.objectContaining({ action: 'export', destination_handle: 'P', name: 'result-copy' }));
  expect(executeDelivery).toHaveBeenCalledWith('S', lastRequest!.command_id);
});

it('shows preflight conflicts and never enables execution for a conflicting apply', async () => {
  const api = client(); vi.mocked(api.receive).mockImplementation(async (_s, _b, _r, body) => record(body.command_id) as never);
  const delivery = vi.fn(async (_s, value) => ({ ...value, local_project_id: 'P', local_session_id: 'S', phase: 'prepared', changes: [], conflicts: ['changed.py'], error_code: 'delivery_conflict', branch_head: null }));
  const executeDelivery = vi.fn(); Object.assign(api, { delivery, executeDelivery });
  render(<CloudResultReceive api={api} sid="S" bid="B" rid="R" inputId="I" files={files} active />);
  fireEvent.click(screen.getByRole('button', { name: '接收到本机' }));
  const action = await screen.findByRole('combobox', { name: '代码交付方式' });
  expect(screen.queryByText(/执行前请暂停本机 Agent/)).toBeNull();
  fireEvent.change(action, { target: { value: 'apply' } });
  expect(screen.getByText(/执行前请暂停本机 Agent 和编辑器对相同文件的写入/)).toBeTruthy();
  expect(screen.queryByRole('textbox', { name: '新目录名' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  expect(await screen.findByText('changed.py')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '确认执行交付' })).toBeNull(); expect(executeDelivery).not.toHaveBeenCalled();
  expect(delivery).toHaveBeenCalledWith('S', expect.objectContaining({ action: 'apply', name: '' }));
});
