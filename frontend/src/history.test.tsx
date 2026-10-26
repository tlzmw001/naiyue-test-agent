import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { ApiError, WebApi } from './api';
import type { SessionView } from './types';
import { useConversation } from './useConversation';

afterEach(cleanup);

function recoveryClient(checkpoint_id = 'cp1') {
  const api = new WebApi('synthetic-test-access');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  const history: SessionView = { session_id: 'old', model_id: 'A', workspace_root: '/work', state: 'failed',
    updated_at: '2026-10-17T10:00:00Z', last_run_status: 'failed', active_tools: [], permission_mode: 'Default',
    messages: [], busy: false, can_run: false, has_compaction: false, active_run_id: null, latest_run_id: null,
    manual_review_required: false, pending_approval: false, live: null, checkpoint_id,
    recovery: { kind: 'interrupted_execution', reason: 'previous_run_unfinished', actions: ['continue'], unconfirmed_tool_call_ids: [] } };
  vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([history]);
  vi.spyOn(api, 'session').mockResolvedValue(history);
  vi.spyOn(api, 'start'); vi.spyOn(api, 'events');
  return { api, history };
}

it('shows saved facts as read-only, never sends on Enter, and allows a new session', async () => {
  const api = new WebApi('synthetic-test-access');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  const value = { session_id: 'old', model_id: 'removed-model', workspace_root: '/work', state: 'running',
    updated_at: '2026-10-17T10:00:00Z', last_run_status: null,
    active_tools: [], permission_mode: 'Default', messages: [
      { id: 'h', type: 'human', content: 'saved request', provisional: false, state: 'complete' },
      { id: 'a', type: 'ai', content: 'saved answer', provisional: false, state: 'complete',
        tool_calls: [{ id: 'call', name: 'edit', args: { path: 'file.txt' } }] },
    ], busy: false, can_run: false, has_compaction: false, active_run_id: null, latest_run_id: null,
    manual_review_required: false, pending_approval: false, live: null,
    recovery: { kind: 'interrupted_execution', reason: 'previous_run_unfinished', actions: [],
      unconfirmed_tool_call_ids: ['call'] } } as SessionView;
  vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([value]);
  vi.spyOn(api, 'session').mockResolvedValue(value);
  vi.spyOn(api, 'create').mockResolvedValue({ session_id: 'new' });
  vi.spyOn(api, 'start'); vi.spyOn(api, 'events'); vi.spyOn(api, 'select'); vi.spyOn(api, 'approve');
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: /removed-model/ }));
  expect(await screen.findByText('只读历史')).toBeTruthy();
  expect(screen.getByText('saved request')).toBeTruthy();
  expect(screen.getByText('saved answer')).toBeTruthy();
  expect(screen.getByText('结果未确认')).toBeTruthy();
  expect((screen.getByRole('button', { name: /模型与推理强度：removed-model/ }) as HTMLButtonElement).disabled).toBe(true);
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'do not execute' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.start).not.toHaveBeenCalled(); expect(api.events).not.toHaveBeenCalled();
  expect(api.select).not.toHaveBeenCalled(); expect(api.approve).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
  await waitFor(() => expect(api.create).toHaveBeenCalledWith('A'));
});

it('automatically recovers on opening, keeps the draft, and waits for a new send', async () => {
  const { api, history } = recoveryClient('checkpoint-current');
  let finish!: (view: SessionView) => void;
  vi.spyOn(api, 'recover').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<App api={api} />);
  const historyRow = await screen.findByRole('button', { name: / A$/ });
  expect(within(historyRow).getByLabelText('任务状态：失败')).toBeTruthy();
  fireEvent.click(historyRow);
  const recover = await screen.findByRole('button', { name: '结束旧运行并继续会话' });
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'retained new input' } });
  await waitFor(() => expect(api.recover).toHaveBeenCalledWith('old', 'checkpoint-current', 'continue'));
  expect((recover as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: /新建会话/ }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.keyDown(input, { key: 'Enter' });
  const ready = { ...history, state: 'ready', can_run: true, recovery: undefined };
  await act(async () => finish(ready));
  expect((input as HTMLTextAreaElement).value).toBe('retained new input');
  await waitFor(() => expect(screen.queryByText('只读历史')).toBeNull());
  expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(false);
  expect(api.start).not.toHaveBeenCalled(); expect(api.events).not.toHaveBeenCalled();
});

it('refreshes a changed checkpoint after failure and requires another explicit recovery', async () => {
  const { api, history } = recoveryClient();
  vi.spyOn(api, 'session').mockResolvedValueOnce(history).mockResolvedValue({ ...history, checkpoint_id: 'cp2' });
  vi.spyOn(api, 'recover').mockRejectedValueOnce(new ApiError('session_save_unconfirmed'))
    .mockResolvedValueOnce({ ...history, state: 'ready', can_run: true, recovery: undefined });
  render(<App api={api} />);
  const historyRow = await screen.findByRole('button', { name: / A$/ });
  expect(within(historyRow).getByLabelText('任务状态：失败')).toBeTruthy();
  fireEvent.click(historyRow);
  const recover = await screen.findByRole('button', { name: '结束旧运行并继续会话' });
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'retained draft' } });
  await waitFor(() => expect(api.session).toHaveBeenCalledTimes(2));
  expect(api.recover).toHaveBeenCalledTimes(1);
  expect((input as HTMLTextAreaElement).value).toBe('retained draft');
  const button = screen.getByRole('button', { name: '结束旧运行并继续会话' });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(api.recover).toHaveBeenNthCalledWith(2, 'old', 'cp2', 'continue'));
  expect(api.start).not.toHaveBeenCalled(); expect(api.events).not.toHaveBeenCalled();
});

it('keeps confirmed recovery usable when only the sidebar refresh fails', async () => {
  const { api, history } = recoveryClient();
  vi.spyOn(api, 'sessions').mockResolvedValueOnce([history]).mockRejectedValueOnce(new Error('list fixture'));
  vi.spyOn(api, 'recover').mockResolvedValue({ ...history, state: 'ready', can_run: true, recovery: undefined });
  render(<App api={api} />);
  const historyRow = await screen.findByRole('button', { name: / A$/ });
  expect(within(historyRow).getByLabelText('任务状态：失败')).toBeTruthy();
  fireEvent.click(historyRow);
  expect(await screen.findByText('旧运行已结束。会话列表刷新未确认，可继续输入新消息。')).toBeTruthy();
  expect(api.session).toHaveBeenCalledTimes(1);
  expect(screen.queryByText('只读历史')).toBeNull();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'new draft' } });
  expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(false);
  expect(api.start).not.toHaveBeenCalled();
});


it('automatically restores approval cards without approving or cancelling the batch', async () => {
  const { api, history } = recoveryClient();
  const saved: SessionView = { ...history, recovery: { ...history.recovery!, kind: 'pending_approval',
    actions: ['restore_approval', 'continue'] } };
  vi.mocked(api.session).mockResolvedValue(saved);
  vi.spyOn(api, 'approve');
  vi.spyOn(api, 'recover').mockResolvedValue({ ...saved, state: 'interrupted', recovery: undefined,
    pending_approval: true, approval_batches: [{ approval_batch_id: 'batch', kind: 'paused_root', agent_path: 'root', agent: null, origin_run_id: 'original-run',
    items: [{ approval_id: 'row', tool_call_id: 'call', tool_name: 'bash', target: 'python -m pytest' }] }] });
  render(<App api={api} />);
  const historyRow = await screen.findByRole('button', { name: / A$/ });
  expect(within(historyRow).getByLabelText('任务状态：失败')).toBeTruthy();
  fireEvent.click(historyRow);
  expect(await screen.findByRole('region', { name: '工具审批' })).toBeTruthy();
  expect(api.recover).toHaveBeenCalledExactlyOnceWith('old', 'cp1', 'restore_approval');
  expect(api.approve).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled();
  expect(api.events).not.toHaveBeenCalled();
});

it('does not recover an obsolete selection when a newer session opened first', async () => {
  const { api, history } = recoveryClient();
  let finish!: (view: SessionView) => void;
  vi.mocked(api.session).mockImplementation(id => id === 'old' ? new Promise(resolve => { finish = resolve; }) :
    Promise.resolve({ ...history, session_id: id, state: 'ready', can_run: true, recovery: undefined }));
  vi.spyOn(api, 'recover');
  const { result } = renderHook(() => useConversation(api));
  await waitFor(() => expect(result.current.model).toBe('A'));
  let first!: Promise<void>;
  act(() => { first = result.current.selectSession('old'); });
  await act(async () => result.current.selectSession('newer'));
  await act(async () => { finish(history); await first; });
  expect(result.current.session?.session_id).toBe('newer');
  expect(api.recover).not.toHaveBeenCalled();
});

it('does not cancel an inapplicable approval until the user explicitly chooses continue', async () => {
  const { api, history } = recoveryClient();
  vi.mocked(api.session).mockResolvedValue({ ...history, recovery: { ...history.recovery!, kind: 'pending_approval',
    actions: ['restore_approval', 'continue'] } });
  vi.spyOn(api, 'recover').mockRejectedValueOnce(new ApiError('approval_restore_unavailable'))
    .mockResolvedValueOnce({ ...history, recovery: undefined, state: 'ready', can_run: true });
  render(<App api={api} />);
  const historyRow = await screen.findByRole('button', { name: / A$/ });
  expect(within(historyRow).getByLabelText('任务状态：失败')).toBeTruthy();
  fireEvent.click(historyRow);
  expect(await screen.findByText(/原审批的目标或权限条件已变化/)).toBeTruthy();
  expect(api.recover).toHaveBeenCalledExactlyOnceWith('old', 'cp1', 'restore_approval');
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'new task draft' } });
  const button = screen.getByRole('button', { name: '结束旧运行并继续会话' });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  await waitFor(() => expect(api.recover).toHaveBeenNthCalledWith(2, 'old', 'cp1', 'continue'));
  await waitFor(() => expect(screen.queryByText('只读历史')).toBeNull());
  expect((input as HTMLTextAreaElement).value).toBe('new task draft');
  expect(api.start).not.toHaveBeenCalled(); expect(api.events).not.toHaveBeenCalled();
});
