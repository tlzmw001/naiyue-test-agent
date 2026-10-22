import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { WebApi } from './api';
import type { SessionView } from './types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const info = { workspace_root: '/work/中文项目', workspace_label: '中文项目' };
const row = { session_id: 's', title: '工作会话', model_id: 'A', workspace_root: '/other/session-project',
  state: 'ready', updated_at: '', last_run_status: null };
const detail: SessionView = { ...row, messages: [], busy: false, can_run: true,
  has_compaction: false, active_run_id: null, latest_run_id: null, manual_review_required: false,
  pending_approval: false, active_tools: [], permission_mode: 'Default', live: null };
function client() {
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(info))));
  vi.stubGlobal('fetch', fetcher);
  const api = new WebApi('synthetic-workspace-access');
  vi.spyOn(api, 'backgroundTasks').mockResolvedValue({ items: [], active_count: 0, terminal_revision: 0, revision: 'empty', next_before: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], permission_modes: ['Default'],
    default_permission_mode: 'Default', default_active_tools: [] });
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  vi.spyOn(api, 'session').mockResolvedValue(detail);
  vi.spyOn(api, 'context').mockResolvedValue({ session_id: 's', current: { basis: 'unavailable', estimate: null }, latest_request: null });
  return { api, fetcher, warning };
}

it('shows the bound workspace on an empty homepage through the authenticated service API', async () => {
  const { api, fetcher, warning } = client();
  render(<App api={api} />);
  expect(await screen.findByText('中文项目')).toBeTruthy();
  expect(screen.getByTitle(info.workspace_root).textContent).toBe('中文项目');
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('/api/service');
  expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', headers: { Authorization: 'Bearer synthetic-workspace-access' } });
  expect(api.prepare).not.toHaveBeenCalled();
  expect(api.session).not.toHaveBeenCalled();
  expect(warning).not.toHaveBeenCalled();
});

it('distinguishes an unbound service from loading and failure', async () => {
  const { api, fetcher } = client();
  fetcher.mockResolvedValue(new Response(JSON.stringify({ workspace_root: null, workspace_label: null })));
  render(<App api={api} />);
  expect(await screen.findByText('未绑定工作区')).toBeTruthy();
  expect(screen.queryByText('工作区信息读取失败')).toBeNull();
});

it('loads model choices independently while service metadata is pending', async () => {
  const { api, fetcher } = client();
  let finish!: (value: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  render(<App api={api} />);
  expect(screen.getByText('正在读取工作区')).toBeTruthy();
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'ready to send' } });
  expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(false);
  await act(async () => finish(new Response(JSON.stringify(info))));
  expect(screen.getByText('中文项目')).toBeTruthy();
});

it.each(['network', 'http', 'json'])('shows a bounded %s failure, retries only metadata and keeps sending available', async failure => {
  const { api, fetcher, warning } = client();
  if (failure === 'network') fetcher.mockRejectedValueOnce(new Error('PRIVATE_NETWORK_DETAIL'));
  else fetcher.mockResolvedValueOnce(new Response(failure === 'http' ? 'PRIVATE_SERVER_DETAIL' : '{invalid',
    { status: failure === 'http' ? 503 : 200 }));
  render(<App api={api} />);
  await screen.findByText('工作区信息读取失败');
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  expect(screen.queryByText(/PRIVATE_/)).toBeNull();
  expect(screen.queryByText('未绑定工作区')).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'ready to send' } });
  expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '重试工作区信息' }));
  await screen.findByText('中文项目');
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(api.models).toHaveBeenCalledTimes(1);
  expect(api.sessions).toHaveBeenCalledTimes(1);
  expect(warning).toHaveBeenCalledExactlyOnceWith('workspace_info_read_failed');
});

it.each([['/other/session-project', 'session-project'], ['C:\\工作\\Windows项目', 'Windows项目'],
  ['/', '/'], ['C:\\', 'C:\\'], ['\\\\server\\share\\', '\\\\server\\share\\'],
  ['\\\\server\\share\\folder', 'folder'], ['/work/<img>', '<img>']])('displays the selected session path %s instead of service identity', async (path, label) => {
  const { api } = client();
  vi.mocked(api.sessions).mockResolvedValue([{ ...row, workspace_root: path }]);
  vi.mocked(api.session).mockResolvedValue({ ...detail, workspace_root: path });
  const { container } = render(<App api={api} />);
  await screen.findByText('中文项目');
  fireEvent.click(screen.getByRole('button', { name: '工作会话 A' }));
  await waitFor(() => expect(screen.getByTitle(path).textContent).toBe(label));
  expect(screen.queryByText('中文项目')).toBeNull();
  expect(container.querySelector('img')).toBeNull();
});

it('preserves a selected session explicitly having no workspace even when the service is bound', async () => {
  const { api } = client();
  vi.mocked(api.sessions).mockResolvedValue([{ ...row, workspace_root: null }]);
  vi.mocked(api.session).mockResolvedValue({ ...detail, workspace_root: null });
  render(<App api={api} />);
  await screen.findByText('中文项目');
  fireEvent.click(screen.getByRole('button', { name: '工作会话 A' }));
  await screen.findByText('未绑定工作区');
  expect(screen.queryByText('中文项目')).toBeNull();
});

it('shows a known session workspace even when reading service metadata failed', async () => {
  const { api, fetcher, warning } = client();
  fetcher.mockRejectedValueOnce(new Error('network unavailable'));
  vi.mocked(api.sessions).mockResolvedValue([row]);
  render(<App api={api} />);
  await screen.findByText('工作区信息读取失败');
  fireEvent.click(screen.getByRole('button', { name: '工作会话 A' }));
  await screen.findByText('session-project');
  expect(screen.queryByText('工作区信息读取失败')).toBeNull();
  expect(screen.queryByRole('button', { name: '重试工作区信息' })).toBeNull();
  expect(warning).toHaveBeenCalledExactlyOnceWith('workspace_info_read_failed');
});

it.each(['resolve', 'reject'])('aborts and ignores a stale metadata %s when the API client changes', async outcome => {
  const { api, fetcher, warning } = client();
  let resolve!: (response: Response) => void;
  let reject!: (error: Error) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((yes, no) => { resolve = yes; reject = no; }));
  const { rerender, unmount } = render(<App api={api} />);
  const oldSignal = fetcher.mock.calls[0][1].signal as AbortSignal;
  const newer = new WebApi('synthetic-new-access');
  vi.spyOn(newer, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(newer, 'sessions').mockResolvedValue([]);
  vi.spyOn(newer, 'sessionControlOptions').mockResolvedValue({ tools: [], permission_modes: ['Default'],
    default_permission_mode: 'Default', default_active_tools: [] });
  rerender(<App api={newer} />);
  await screen.findByText('中文项目');
  expect(oldSignal.aborted).toBe(true);
  await act(async () => { if (outcome === 'resolve') resolve(new Response(JSON.stringify({ workspace_root: '/old', workspace_label: 'OLD_RESULT' })));
    else reject(new Error('old failure')); });
  expect(screen.getByText('中文项目')).toBeTruthy();
  expect(screen.queryByText('OLD_RESULT')).toBeNull();
  expect(screen.queryByText('工作区信息读取失败')).toBeNull();
  expect(warning).not.toHaveBeenCalled();
  const currentSignal = fetcher.mock.calls.at(-1)![1].signal as AbortSignal;
  unmount();
  expect(currentSignal.aborted).toBe(true);
});
