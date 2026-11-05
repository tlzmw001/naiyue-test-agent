import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from '../api';
import type { ProjectRow, RegistrationOperation } from '../projectTypes';
import ProjectRemoval from './ProjectRemoval';

beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value() { this.setAttribute('open', ''); } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value() { this.removeAttribute('open'); } });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
afterAll(() => { delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal; delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close; });
const project: ProjectRow = { project_id: '11111111-1111-1111-1111-111111111111', name: '项目 A', source: { path: 'C:\\work\\项目' },
  lifecycle: 'active', trusted: true, revision: 1, availability: 'available' };
function setup() {
  const api = new WebApi('synthetic', 'chats');
  vi.spyOn(api, 'projectOperations').mockResolvedValue({ items: [] });
  vi.spyOn(api, 'removeProject').mockResolvedValue({ project_id: project.project_id, data_retained: true });
  vi.spyOn(api, 'reconcileProject').mockResolvedValue({ items: [] });
  const removed = vi.fn(), close = vi.fn();
  render(<ProjectRemoval api={api} project={project} removed={removed} close={close} />);
  return { api, removed, close };
}
function saved(id: string, stage = 'done'): RegistrationOperation {
  return { operation_id: id, action: 'remove', project_id: project.project_id, stage, error_code: null,
    result: stage === 'done' ? { project_id: project.project_id, data_retained: true } : null };
}
it('reads the original durable receipt after a lost acknowledgement without another delete', async () => {
  const { api, removed } = setup();
  vi.mocked(api.removeProject).mockRejectedValue(new TypeError('network'));
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await screen.findByText(/尚未确认项目是否已移除/);
  const id = vi.mocked(api.removeProject).mock.calls[0][1];
  vi.mocked(api.projectOperations).mockResolvedValue({ items: [saved(id)] });
  fireEvent.click(screen.getByRole('button', { name: '核对操作结果' }));
  await waitFor(() => expect(removed).toHaveBeenCalledWith(project.project_id));
  expect(api.removeProject).toHaveBeenCalledTimes(1);
});
it('keeps the original command ID when neither response nor durable operation is known', async () => {
  const { api, removed } = setup();
  vi.mocked(api.removeProject).mockRejectedValueOnce(new TypeError('network'));
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await screen.findByText(/尚未确认项目是否已移除/);
  fireEvent.click(screen.getByRole('button', { name: '重试移除' }));
  await waitFor(() => expect(removed).toHaveBeenCalledOnce());
  const calls = vi.mocked(api.removeProject).mock.calls;
  expect(calls[0][1]).toBe(calls[1][1]);
});
it.each(['project_in_use', 'project_cloud_tasks_retained'])('preserves a rejected project and permits a fresh attempt after %s', async code => {
  const { api, removed } = setup();
  vi.mocked(api.removeProject).mockRejectedValueOnce(new ApiError(code));
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await screen.findByText(code === 'project_in_use' ? /项目中还有运行/ : /项目仍关联云端任务/);
  expect(removed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '重试移除' }));
  await waitFor(() => expect(removed).toHaveBeenCalledOnce());
  const calls = vi.mocked(api.removeProject).mock.calls;
  expect(calls[0][1]).not.toBe(calls[1][1]);
});
it('resumes a pending removal by its saved ID and verifies its final receipt', async () => {
  const { api, removed } = setup();
  vi.mocked(api.projectOperations).mockResolvedValueOnce({ items: [saved('pending', 'reserved')] })
    .mockResolvedValue({ items: [saved('pending')] });
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await waitFor(() => expect(removed).toHaveBeenCalledOnce());
  expect(api.reconcileProject).toHaveBeenCalledWith('pending');
  expect(api.removeProject).not.toHaveBeenCalled();
});
it('does not accept another action or an incomplete success payload', async () => {
  const { api, removed } = setup();
  vi.mocked(api.removeProject).mockResolvedValue({ project_id: project.project_id, data_retained: false });
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await screen.findByText(/尚未确认项目是否已移除/);
  const id = vi.mocked(api.removeProject).mock.calls[0][1];
  vi.mocked(api.projectOperations).mockResolvedValue({ items: [{ ...saved(id), action: 'add_local' }] });
  fireEvent.click(screen.getByRole('button', { name: '核对操作结果' }));
  await waitFor(() => expect(api.projectOperations).toHaveBeenCalledTimes(2));
  expect(removed).not.toHaveBeenCalled();
});
it('blocks duplicate clicks and closing until the accepted request settles', async () => {
  const { api, removed, close } = setup();
  let finish!: (value: { project_id: string; data_retained: boolean }) => void;
  vi.mocked(api.removeProject).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '确认移除' }));
  await waitFor(() => expect(api.removeProject).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: '正在处理…' }));
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(api.removeProject).toHaveBeenCalledOnce(); expect(close).not.toHaveBeenCalled();
  await act(async () => finish({ project_id: project.project_id, data_retained: true }));
  expect(removed).toHaveBeenCalledOnce();
});
