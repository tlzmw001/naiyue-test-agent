import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CloudCodeDelivery from './CloudCodeDelivery';
import { ApiError } from '../api';
import type { CloudApi } from '../cloudApi';
import type { DeliveryRecord, DeliveryRequest } from '../cloudDeliveryTypes';

type Request = Omit<DeliveryRequest, 'local_session_id'>;
const original: Request = { command_id: 'D', receive_id: 'C', action: 'apply', destination_handle: 'P', name: '' };
function row(request: Request, phase: DeliveryRecord['phase'] = 'prepared'): DeliveryRecord {
  return { command_id: request.command_id, receive_id: request.receive_id, action: request.action, name: request.name,
    local_project_id: 'P', local_session_id: 'S', phase, changes: [{ path: 'updated.py', operation: 'modify' }], conflicts: [], error_code: null, branch_head: null };
}
function client() {
  return { projectId: 'P', delivery: vi.fn(async (_s, value: Request) => row(value)),
    deliveryStatus: vi.fn().mockRejectedValue(new ApiError('cloud_delivery_not_found')), executeDelivery: vi.fn() } as unknown as CloudApi;
}
afterEach(() => { cleanup(); sessionStorage.clear(); });
async function prepare(api: CloudApi) {
  render(<CloudCodeDelivery api={api} sid="S" receiveId="C" resultKey="B.R" active />);
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  await screen.findByText('预检已完成，等待确认');
}
function confirm(name = '确认执行交付') {
  fireEvent.click(screen.getByRole('checkbox', { name: '我已核对目标和变化，确认执行此交付' }));
  fireEvent.click(screen.getByRole('button', { name }));
}
it('polls preparation without writing and enables confirmation only after the verified preflight', async () => {
  const api = client(); let release!: (value: DeliveryRecord) => void; let fixed!: Request;
  vi.mocked(api.delivery).mockImplementation(async (_s, value) => { fixed = value; return row(value, 'preparing'); });
  vi.mocked(api.deliveryStatus).mockImplementation(() => new Promise(done => { release = done; }));
  render(<CloudCodeDelivery api={api} sid="S" receiveId="C" resultKey="B.R" active />);
  expect(screen.queryByText(/先验证固定输入和补丁/)).toBeNull();
  expect(screen.queryByText(/只填写当前项目内的一个新目录名/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  await screen.findByText('正在验证代码交付'); await waitFor(() => expect(api.deliveryStatus).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: '确认执行交付' })).toBeNull(); expect(api.executeDelivery).not.toHaveBeenCalled();
  await act(async () => release(row(fixed)));
  expect(await screen.findByRole('button', { name: '确认执行交付' })).toHaveProperty('disabled', true);
});
it('preserves the original preparation command and locks its fields after a lost ACK', async () => {
  const api = client(); vi.mocked(api.delivery).mockRejectedValueOnce(new TypeError('lost preparation ACK')).mockImplementation(async (_s, value) => row(value));
  render(<CloudCodeDelivery api={api} sid="S" receiveId="C" resultKey="B.R" active />);
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '核对原预检' })).toHaveProperty('disabled', false));
  expect(screen.getByRole('combobox', { name: '代码交付方式' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '核对原预检' }));
  await screen.findByText('预检已完成，等待确认');
  expect(vi.mocked(api.delivery).mock.calls[1]).toEqual(vi.mocked(api.delivery).mock.calls[0]); expect(api.executeDelivery).not.toHaveBeenCalled();
});

it('keeps the typed name editable after a definitive pre-admission validation rejection', async () => {
  const api = client(); vi.mocked(api.delivery).mockRejectedValueOnce(new ApiError('cloud_delivery_name_invalid')).mockImplementation(async (_s, value) => row(value));
  render(<CloudCodeDelivery api={api} sid="S" receiveId="C" resultKey="B.R" active branchAvailable />);
  fireEvent.change(screen.getByRole('combobox', { name: '代码交付方式' }), { target: { value: 'branch' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Git 分支名' }), { target: { value: 'bad/path' } });
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Git 分支名' })).toHaveProperty('disabled', false));
  expect(screen.getByRole('textbox', { name: 'Git 分支名' })).toHaveProperty('value', 'bad/path');
  fireEvent.change(screen.getByRole('textbox', { name: 'Git 分支名' }), { target: { value: 'safe-copy' } });
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' })); await screen.findByText('预检已完成，等待确认');
  expect(vi.mocked(api.delivery).mock.calls[1][1].command_id).not.toBe(vi.mocked(api.delivery).mock.calls[0][1].command_id);
  expect(api.executeDelivery).not.toHaveBeenCalled();
});
it('restores an unknown operation by reading it and explicitly continues partial execution with the same ID', async () => {
  sessionStorage.setItem('naiyue.cloud-delivery.P.S.B.R', JSON.stringify(original));
  const api = client(); vi.mocked(api.deliveryStatus).mockResolvedValue(row(original, 'unknown'));
  vi.mocked(api.executeDelivery).mockResolvedValueOnce(row(original, 'partial')).mockResolvedValueOnce(row(original, 'completed'));
  render(<CloudCodeDelivery api={api} sid="S" receiveId="C" resultKey="B.R" active />);
  await screen.findByText('代码交付状态待核对'); expect(api.delivery).not.toHaveBeenCalled(); expect(api.executeDelivery).not.toHaveBeenCalled();
  confirm('确认继续原交付'); await screen.findByText('代码交付部分完成');
  expect(screen.getByRole('button', { name: '确认继续原交付' })).toHaveProperty('disabled', true);
  confirm('确认继续原交付'); await screen.findByText('代码交付已完成');
  expect(vi.mocked(api.executeDelivery).mock.calls).toEqual([['S', 'D', false], ['S', 'D', false]]);
});
it('queries a lost execution ACK instead of automatically executing again', async () => {
  const api = client(); await prepare(api); const request = vi.mocked(api.delivery).mock.calls[0][1];
  vi.mocked(api.executeDelivery).mockRejectedValueOnce(new TypeError('lost execution ACK'));
  confirm(); await waitFor(() => expect(screen.queryByRole('button', { name: '确认执行交付' })).toBeNull());
  vi.mocked(api.deliveryStatus).mockResolvedValue(row(request, 'completed'));
  fireEvent.click(screen.getByRole('button', { name: '查询原交付状态' }));
  await screen.findByText('代码交付已完成'); expect(api.executeDelivery).toHaveBeenCalledOnce();
  expect(api.deliveryStatus).toHaveBeenCalledWith('S', request.command_id);
});
it('sends an explicit branch name and displays the returned branch head', async () => {
  const api = client(); let request!: Request;
  vi.mocked(api.delivery).mockImplementation(async (_s, value) => { request = value; return row(value); });
  vi.mocked(api.executeDelivery).mockImplementation(async () => ({ ...row(request, 'completed'), branch_head: 'f'.repeat(40) }));
  render(<CloudCodeDelivery api={api} sid="S" receiveId="C" resultKey="B.R" active branchAvailable />);
  fireEvent.change(screen.getByRole('combobox', { name: '代码交付方式' }), { target: { value: 'branch' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Git 分支名' }), { target: { value: 'cloud/fix-tests' } });
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' })); await screen.findByText('预检已完成，等待确认');
  expect(request.action).toBe('branch'); expect(request.name).toBe('cloud/fix-tests'); expect(api.executeDelivery).not.toHaveBeenCalled();
  confirm(); expect(await screen.findByText('已记录分支提交 ' + 'f'.repeat(40))).toBeTruthy();
});
it('ignores a late preparation response after the parent result changes', async () => {
  const api = client(); let finish!: (value: DeliveryRecord) => void;
  vi.mocked(api.delivery).mockImplementation(() => new Promise(done => { finish = done; }));
  const view = render(<CloudCodeDelivery key="R" api={api} sid="S" receiveId="C" resultKey="B.R" active />);
  fireEvent.click(screen.getByRole('button', { name: '预检代码交付' })); const request = vi.mocked(api.delivery).mock.calls[0][1];
  view.rerender(<CloudCodeDelivery key="other" api={api} sid="other" receiveId="C2" resultKey="B.R2" active />);
  await act(async () => finish(row(request)));
  expect(screen.queryByText('修改 · updated.py')).toBeNull(); expect(screen.getByRole('combobox', { name: '代码交付方式' })).toHaveProperty('value', 'apply');
  expect(api.executeDelivery).not.toHaveBeenCalled();
});
it('continues a restored forced partial using its original confirmation and command', async () => {
  sessionStorage.setItem('naiyue.cloud-delivery.P.S.B.R', JSON.stringify(original));
  const api = client(); vi.mocked(api.deliveryStatus).mockResolvedValue({ ...row(original, 'partial'), force: true, force_allowed: true, conflicts: ['updated.py'] });
  vi.mocked(api.executeDelivery).mockResolvedValue({ ...row(original, 'completed'), force: true });
  render(<CloudCodeDelivery api={api} sid="S" receiveId="C" resultKey="B.R" active />);
  await screen.findByText('代码交付部分完成'); confirm('确认继续原交付');
  await screen.findByText('代码交付已完成'); expect(api.executeDelivery).toHaveBeenCalledExactlyOnceWith('S', 'D', true);
});
