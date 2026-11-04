import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import CloudTasks from './CloudTasks';
import CloudTaskDetail from './CloudTaskDetail';
import { installDialogMock } from '../testDialogs';
import type { CloudApi } from '../cloudApi';
import type { CloudBinding, CloudSnapshot, CloudTask } from '../cloudTypes';
import { useCloudObservation } from '../useCloudObservation';
import { useCloudDraft } from '../useCloudDraft';
import CloudTaskCreate from './CloudTaskCreate';
import { ApiError } from '../api';
import CloudTaskActions from './CloudTaskActions';
afterEach(() => { cleanup(); sessionStorage.clear(); });
const project = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const task: CloudTask = { task_id: 'a'.repeat(32), project_id: project, input_id: 'b'.repeat(64), generation: 1, revision: 1,
  lifecycle: 'ready', runner_phase: 'ready', environment_phase: 'backend_ready', sandbox_id: 'container', active_run_id: null,
  last_run_id: null, last_error_code: null, last_quiescent_at: 1, environment_operation_id: null, control_operation_id: null,
  artifact_operation_id: null, runner_model_ids: ['A'], reclaim_blockers: [] };
const binding: CloudBinding = { binding_id: 'B', local_session_id: 'S', profile_id: 'work', instance_id: 'I', task_id: task.task_id,
  input_id: task.input_id, revision: 1, preparation: 'prepared', retained: true, create_command_id: 'C', cloud: task };
const snapshot: CloudSnapshot = { task, cursor: 'cursor', archive: { epoch: 'history', coverage_seq: 1,
  messages: [{ id: 'm', type: 'ai', content: '云端正在检查测试', provisional: false, state: 'complete' }],
  next_cursor: null, thread: { thread_id: 'root' } },
  live: { epoch: 'live', seq: 1, replace: true, available: true, capture_incomplete: false, messages: [], tools: [], threads: [] } };
function api(rows: CloudBinding[] = []) {
  return { projectId: project, profiles: vi.fn().mockResolvedValue([{ profile_id: 'work', template_ids: ['python'], default_template: 'python' }]),
    sourceTree: vi.fn().mockResolvedValue({ git: true, git_error: null, path: '', entries: [], next_offset: null, excluded_count: 0 }),
    sourcePreview: vi.fn().mockResolvedValue({ file_count: 1, total_bytes: 1, issues: [], limits: { max_input_bytes: 2147483648 } }),
    list: vi.fn().mockResolvedValue(rows), binding: vi.fn().mockImplementation((_sid, bid) => Promise.resolve(rows.find(r => r.binding_id === bid))),
    snapshot: vi.fn().mockResolvedValue(snapshot), threads: vi.fn().mockResolvedValue({ threads: [], next_cursor: null }),
    usage: vi.fn().mockResolvedValue({ recorded_calls: 0, capture: { capture_incomplete: false } }),
    models: vi.fn().mockResolvedValue({ default_model_id: 'A', model_ids: ['A', 'B'], thinking_options: {
      A: { default_level: 'low', supported_levels: ['low', 'high'] }, B: { default_level: 'model_default', supported_levels: [] } } }),
    draft: vi.fn().mockResolvedValue({ text: '', model_id: 'A', draft_revision: 0 }), saveDraft: vi.fn().mockImplementation((_sid, _bid, d) => Promise.resolve({ ...d, draft_revision: 1 })),
    results: vi.fn().mockResolvedValue([]), operations: vi.fn().mockResolvedValue([]), source: vi.fn(), create: vi.fn(), message: vi.fn(), command: vi.fn(),
    observe: vi.fn().mockImplementation(async (_sid, _bid, _thread, _cursor, signal: AbortSignal) => new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }))),
  } as unknown as CloudApi;
}
it('opens an empty real list without creating a task or calling a model', async () => {
  const cloud = api(); render(<CloudTasks api={cloud} sessionId="S" active />);
  await screen.findByText('还没有云端任务');
  expect(screen.queryByText('每个任务使用独立沙箱。打开和切换只查看，不创建环境。')).toBeNull();
  expect(cloud.create).not.toHaveBeenCalled(); expect(cloud.source).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '新增任务' }));
  expect(await screen.findByRole('button', { name: '导入并准备任务' })).toBeTruthy();
  expect(screen.queryByText(/只导入项目文件并准备沙箱/)).toBeNull();
  expect(screen.queryByText(/后续消息复用云端工作区/)).toBeNull();
  expect(cloud.source).not.toHaveBeenCalled();
});
it('reuses the main model menu and saves the cloud thinking selection independently', async () => {
  const cloud = api([binding]); render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  fireEvent.click(await screen.findByRole('button', { name: '模型与推理强度：A · low' }));
  fireEvent.click(screen.getByRole('radio', { name: /^high$/ }));
  expect(screen.getByRole('button', { name: '模型与推理强度：A · high' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '关闭模型菜单' }));
  fireEvent.change(screen.getByRole('textbox', { name: '发给此云端任务' }), { target: { value: '云端独立请求' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
  await waitFor(() => expect(cloud.saveDraft).toHaveBeenCalledWith('S', 'B', expect.objectContaining({ thinking_level: 'high' })));
  fireEvent.click(screen.getByRole('button', { name: '模型与推理强度：A · high' }));
  fireEvent.click(screen.getByRole('radio', { name: /^B$/ }));
  expect(screen.getByRole('radio', { name: /^配置默认$/ })).toHaveProperty('checked', true);
  expect(screen.getByText('当前模型未配置可切换的推理档位。')).toBeTruthy();
});
it('shows a retained task history and a separate target composer', async () => {
  const cloud = api([binding]); render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  expect(await screen.findByText('云端正在检查测试')).toBeTruthy();
  expect(screen.queryByText(/保存草稿到本机/)).toBeNull();
  expect(screen.getByRole('textbox', { name: '发给此云端任务' })).toBeTruthy();
  expect(cloud.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '返回任务列表' }));
  expect(await screen.findByRole('button', { name: /任务 aaaaaaaa/ })).toBe(document.activeElement);
});
it('keeps cloud drafts isolated on task switches and never sends while a run is active', async () => {
  const b2 = { ...binding, binding_id: 'B2', task_id: 'c'.repeat(32), cloud: { ...task, task_id: 'c'.repeat(32), active_run_id: 'r' } };
  const cloud = api([binding, b2]); render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  fireEvent.change(await screen.findByRole('textbox', { name: '发给此云端任务' }), { target: { value: '仅第一个任务的提示' } });
  fireEvent.click(screen.getByRole('button', { name: '返回任务列表' }));
  fireEvent.click(await screen.findByRole('button', { name: /任务 cccccccc/ }));
  expect(await screen.findByRole('textbox', { name: '发给此云端任务' })).toHaveProperty('value', '');
  expect(screen.getByRole('button', { name: '发送到云端' })).toHaveProperty('disabled', true);
  fireEvent.click(screen.getByRole('button', { name: '返回任务列表' }));
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '发给此云端任务' })).toHaveProperty('value', '仅第一个任务的提示'));
});
it('marks cached metadata stale and prevents controls until owner metadata can be read', async () => {
  const cloud = api([binding]); vi.mocked(cloud.binding).mockRejectedValue(new Error('offline'));
  render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  await screen.findByText(/任务状态尚未确认/);
  await waitFor(() => expect(screen.getByRole('textbox', { name: '发给此云端任务' })).not.toHaveProperty('disabled', true));
  fireEvent.change(screen.getByRole('textbox', { name: '发给此云端任务' }), { target: { value: '不要误发' } });
  expect(screen.getByRole('button', { name: '发送到云端' })).toHaveProperty('disabled', true);
  expect(screen.getByRole('button', { name: '释放环境' })).toHaveProperty('disabled', true);
});
it('reuses the frozen source and command after a lost creation response', async () => {
  const cloud = api(); const created = vi.fn();
  vi.mocked(cloud.source).mockResolvedValue({ source_handle: 'H', input_id: task.input_id, size: 1 });
  vi.mocked(cloud.create).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ binding, operation: { command_id: 'C' } } as never);
  render(<CloudTaskCreate api={cloud} sessionId="S" profiles={[{ profile_id: 'work', template_ids: ['python'], default_template: 'python', connection: 'ssh', tunnel: 'private' }]} back={vi.fn()} created={created} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '预览导入范围' })).not.toHaveProperty('disabled', true));
  fireEvent.click(screen.getByRole('button', { name: '预览导入范围' }));
  await screen.findByText(/1 个文件/);
  fireEvent.click(screen.getByRole('button', { name: '导入并准备任务' }));
  fireEvent.click(await screen.findByRole('button', { name: '重试原准备操作' }));
  await waitFor(() => expect(created).toHaveBeenCalledWith(binding));
  expect(cloud.source).toHaveBeenCalledTimes(1);
  expect(vi.mocked(cloud.create).mock.calls[1][0]).toEqual(vi.mocked(cloud.create).mock.calls[0][0]);
});
it('paginates threads and preserves the oldest archive cursor when a snapshot replaces live data', async () => {
  const cloud = api(); let accept: Parameters<CloudApi['observe']>[5] = () => {};
  vi.mocked(cloud.snapshot).mockResolvedValue({ ...snapshot, archive: { ...snapshot.archive, next_cursor: 'older' } });
  vi.mocked(cloud.threads).mockResolvedValueOnce({ threads: [{ thread_id: 'child1', role: 'child' }], next_cursor: 'threads2' })
    .mockResolvedValue({ threads: [{ thread_id: 'child2', role: 'child' }], next_cursor: null });
  cloud.history = vi.fn().mockResolvedValue({ ...snapshot.archive, messages: [{ id: 'old', sequence: -1, type: 'human', content: 'old' }], next_cursor: 'oldest' });
  vi.mocked(cloud.observe).mockImplementation(async (_s, _b, _t, _c, signal, callback) => {
    accept = callback; await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }));
  });
  const { result } = renderHook(() => useCloudObservation(cloud, 'S', 'B', task.task_id, true));
  await waitFor(() => expect(result.current.threads).toHaveLength(1));
  await act(async () => { await result.current.loadThreads(); await result.current.loadOlder(); });
  expect(result.current.threads.map(t => t.thread_id)).toEqual(['child1', 'child2']);
  act(() => accept({ event: 'snapshot', data: snapshot }));
  await act(async () => { await result.current.loadOlder(); });
  expect(vi.mocked(cloud.history).mock.calls[1][3]).toBe('oldest');
});
it('cleans observation on hiding and ignores late snapshots for the old task', async () => {
  const cloud = api(); let finish!: (value: CloudSnapshot) => void;
  vi.mocked(cloud.snapshot).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const { result, rerender } = renderHook(({ active }) => useCloudObservation(cloud, 'S', 'B', task.task_id, active), { initialProps: { active: true } });
  rerender({ active: false });
  await act(async () => { finish(snapshot); });
  expect(result.current.snapshot).toBeUndefined(); expect(cloud.observe).not.toHaveBeenCalled();
});
it('checks detail identity and rejects cancellation instead of returning partial content', async () => {
  const cloud = api(); const controller = new AbortController();
  const row = { ...snapshot.archive.messages[0], detail: { message_id: 'm', thread_id: 'root', revision: 1 } };
  cloud.detail = vi.fn().mockImplementation(async () => {
    controller.abort(); return { ...row.detail, field: 'content', offset: 0, next_offset: 2, text: '文😀', body_sha256: 'H' };
  });
  const { result } = renderHook(() => useCloudObservation(cloud, 'S', 'B', task.task_id, true));
  await expect(result.current.content(row, 'content', controller.signal)).rejects.toHaveProperty('name', 'AbortError');
  vi.mocked(cloud.detail).mockResolvedValue({ ...row.detail, field: 'reasoning', offset: 0, next_offset: null, text: 'wrong field', body_sha256: 'H' });
  await expect(result.current.content(row, 'content')).rejects.toThrow('cloud_detail_identity_mismatch');
});
it('does not erase a newer task draft when an older message is acknowledged', async () => {
  const cloud = api(); const { result } = renderHook(() => useCloudDraft(cloud, 'S', 'B'));
  await waitFor(() => expect(result.current.ready).toBe(true));
  act(() => result.current.setText('first')); const version = result.current.version;
  act(() => result.current.setText('next'));
  await act(async () => { await result.current.acknowledged(version); });
  expect(result.current.text).toBe('next');
});
it('preserves text and unlocks a definitively rejected send for a new submission', async () => {
  const cloud = api([binding]); vi.mocked(cloud.message).mockRejectedValue(new ApiError('cloud_binding_conflict'));
  render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  const box = await screen.findByRole('textbox', { name: '发给此云端任务' });
  await waitFor(() => expect(box).not.toHaveProperty('disabled', true));
  fireEvent.change(box, { target: { value: '保留此提示' } });
  fireEvent.click(screen.getByRole('button', { name: '发送到云端' }));
  await waitFor(() => expect(cloud.message).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole('button', { name: '发送到云端' })).not.toHaveProperty('disabled', true));
  expect(box).toHaveProperty('value', '保留此提示');
  expect(screen.queryByRole('button', { name: '核对并重试原请求' })).toBeNull();
});
it('selects the first available profile when profiles arrive after the create form opens', async () => {
  const cloud = api(); const props = { api: cloud, sessionId: 'S', back: vi.fn(), created: vi.fn() };
  const { rerender } = render(<CloudTaskCreate {...props} profiles={[]} />);
  rerender(<CloudTaskCreate {...props} profiles={[{ profile_id: 'work', template_ids: ['python'], default_template: 'python', connection: 'ssh', tunnel: 'private' }]} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '预览导入范围' })).not.toHaveProperty('disabled', true));
  fireEvent.click(screen.getByRole('button', { name: '预览导入范围' }));
  await screen.findByText(/1 个文件/);
  expect(screen.getByRole('button', { name: '导入并准备任务' })).not.toHaveProperty('disabled', true);
});
it('offers a live child before its durable thread has been archived', async () => {
  const cloud = api([binding]);
  vi.mocked(cloud.snapshot).mockResolvedValue({ ...snapshot, live: { ...snapshot.live, threads: [{ thread_id: 'child-live', source: {
    agent_id: 'child-li', agent_path: 'root/child', agent_run_id: 'child-run', parent_run_id: 'run', parent_tool_call_id: 'call' } }] } });
  render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  expect(await screen.findByRole('option', { name: '子任务 child-li' })).toBeTruthy();
});
it('requires explicit confirmation before releasing a disconnected environment', async () => {
  // JSDOM lacks native dialog methods; only emulate their open/close state here.
  const restoreDialog = installDialogMock();
  try {
    const execute = vi.fn().mockResolvedValue(undefined); const cloud = api([binding]);
    const uncertain = { ...binding, cloud: { ...task, runner_phase: 'disconnected', last_quiescent_at: null, reclaim_blockers: ['runner_disconnected'] } };
    render(<CloudTaskActions api={cloud} sid="S" binding={uncertain} busy={false} execute={execute} />);
    fireEvent.click(screen.getByRole('button', { name: '核对并释放环境' }));
    expect(screen.getByRole('button', { name: '确认释放此环境' })).toHaveProperty('disabled', true);
    expect(execute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '确认释放此环境' }));
    await waitFor(() => expect(execute).toHaveBeenCalledWith('environment', { action: 'release', expected_binding_revision: 1, expected_generation: 1, acknowledge_unknown: true }));
  } finally {
    restoreDialog();
  }
});
it('names background-only stopping by the actual affected work', () => {
  render(<CloudTaskActions api={api()} sid="S" binding={{ ...binding, cloud: { ...task, reclaim_blockers: ['background_commands'] } }} busy={false} execute={vi.fn()} />);
  expect(screen.getByRole('button', { name: '停止后台任务' })).not.toHaveProperty('disabled', true);
  expect(screen.queryByRole('button', { name: '停止本轮' })).toBeNull();
});
it('makes a rejected command an alert and preserves the submitted draft', async () => {
  const cloud = api([binding]);
  vi.mocked(cloud.message).mockImplementation(async (_sid, _bid, body) => ({ command_id: String((body as { command_id: string }).command_id), binding_id: 'B', kind: 'message', phase: 'rejected', error_code: 'task_revision_conflict', steps: {} }));
  render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  const box = await screen.findByRole('textbox', { name: '发给此云端任务' });
  fireEvent.change(box, { target: { value: '请继续' } });
  fireEvent.click(screen.getByRole('button', { name: '发送到云端' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('云端状态已变化'));
  expect(box).toHaveProperty('value', '请继续');
});
it('shows submitted text immediately while the request is awaiting confirmation', async () => {
  const cloud = api([binding]); vi.mocked(cloud.message).mockImplementation(() => new Promise(() => {}));
  render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  fireEvent.change(await screen.findByRole('textbox', { name: '发给此云端任务' }), { target: { value: '立即显示的消息' } });
  fireEvent.click(screen.getByRole('button', { name: '发送到云端' }));
  expect(await screen.findByText('立即显示的消息', { selector: 'p' })).toBeTruthy();
  expect(screen.getByText('正在提交，等待云端确认')).toBeTruthy();
});
it('keeps an accepted message visible until its saved human row arrives', async () => {
  const cloud = api([binding]); let accepted = false;
  const op = { command_id: 'sent', binding_id: 'B', kind: 'message', phase: 'accepted', run_id: 'r1', steps: {} };
  vi.mocked(cloud.message).mockImplementation(async (_sid, _bid, body) => { accepted = true; op.command_id = (body as { command_id: string }).command_id; return op; });
  vi.mocked(cloud.operations).mockImplementation(async () => accepted ? [op] : []);
  render(<CloudTasks api={cloud} sessionId="S" active />);
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  const box = await screen.findByRole('textbox', { name: '发给此云端任务' });
  fireEvent.change(box, { target: { value: '等待历史同步的消息' } }); fireEvent.click(screen.getByRole('button', { name: '发送到云端' }));
  expect(await screen.findByText('云端已受理，等待消息同步')).toBeTruthy();
  expect(screen.getByText('等待历史同步的消息', { selector: 'p' })).toBeTruthy();
});

it('confirms background stopping and cancels without dispatch', async () => {
  const restore = installDialogMock();
  try {
    const execute = vi.fn().mockResolvedValue(true);
    render(<CloudTaskActions api={api()} sid="S" binding={{ ...binding, cloud: { ...task, last_run_id: 'old-run', reclaim_blockers: ['background_commands'] } }} busy={false} execute={execute} />);
    fireEvent.click(screen.getByRole('button', { name: '停止后台任务' }));
    expect(execute).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: '停止此任务的后台服务？' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(execute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '停止后台任务' }));
    fireEvent.click(screen.getByRole('button', { name: '确认停止后台任务' }));
    await waitFor(() => expect(execute).toHaveBeenCalledExactlyOnceWith('control', { expected_binding_revision: 1, kind: 'stop', run_id: 'old-run', checkpoint_id: null }));
  } finally { restore(); }
});

it('settles a lost message response by original ID without retaining retry intent', async () => {
  const cloud = api([binding]); let sentId = '';
  vi.mocked(cloud.message).mockImplementation(async (_sid, _bid, body) => {
    sentId = String((body as { command_id: string }).command_id);
    throw new ApiError('cloud_connection_failed');
  });
  const props = { api: cloud, sid: 'S', binding, active: true, back: vi.fn(), changed: vi.fn() };
  const view = render(<CloudTaskDetail {...props} />);
  const input = await screen.findByRole('textbox', { name: '发给此云端任务' });
  await waitFor(() => expect(input).not.toHaveProperty('disabled', true));
  fireEvent.change(input, { target: { value: '原消息' } });
  fireEvent.click(screen.getByRole('button', { name: '发送到云端' }));
  await waitFor(() => expect(cloud.message).toHaveBeenCalledTimes(1));
  await screen.findByRole('button', { name: '核对并重试原请求' });
  fireEvent.change(input, { target: { value: '后续草稿' } });
  vi.mocked(cloud.operations).mockResolvedValue([{ command_id: sentId, binding_id: 'B', kind: 'message', phase: 'accepted', steps: {}, run_id: 'run' }]);
  vi.mocked(cloud.draft).mockResolvedValue({ text: '', model_id: 'A', draft_revision: 2, thinking_level: 'low' });
  view.rerender(<CloudTaskDetail {...props} active={false} />);
  view.rerender(<CloudTaskDetail {...props} />);
  await screen.findByText('云端已受理，等待消息同步');
  expect(input).toHaveProperty('value', '后续草稿');
  expect(screen.queryByRole('button', { name: '核对并重试原请求' })).toBeNull();
  expect(cloud.message).toHaveBeenCalledTimes(1);
});

it('invalidates background stop confirmation when task revision changes', () => {
  const restore = installDialogMock();
  try {
    const execute = vi.fn(); const value = { ...binding, cloud: { ...task, last_run_id: 'old-run', reclaim_blockers: ['background_commands'] } };
    const view = render(<CloudTaskActions api={api()} sid="S" binding={value} busy={false} execute={execute} />);
    fireEvent.click(screen.getByRole('button', { name: '停止后台任务' }));
    view.rerender(<CloudTaskActions api={api()} sid="S" binding={{ ...value, revision: 2 }} busy={false} execute={execute} />);
    expect(screen.getByRole('button', { name: '确认停止后台任务' })).toHaveProperty('disabled', true);
    expect(execute).not.toHaveBeenCalled();
  } finally { restore(); }
});

it('uses the persisted readable task title in the conversation header', async () => {
  render(<CloudTaskDetail api={api()} sid="S" binding={{ ...binding, cloud: { ...task, title: '检查接口与报告' } }} active back={vi.fn()} changed={vi.fn()} />);
  expect(await screen.findByText('检查接口与报告')).toBeTruthy();
});
