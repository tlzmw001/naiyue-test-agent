import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { ApiError, WebApi } from './api';
import type { SessionView } from './types';

afterEach(cleanup);

function setup() {
  const api = new WebApi('fixture');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  const row: SessionView = { session_id: 's', model_id: 'A', thinking_level: 'model_default', workspace_root: null,
    state: 'ready', updated_at: '', last_run_status: null, messages: [], busy: false, can_run: true,
    has_compaction: false, active_run_id: null, latest_run_id: null, active_tools: [], permission_mode: 'Default',
    manual_review_required: false, pending_approval: false, live: null };
  vi.spyOn(api, 'backgroundTasks').mockResolvedValue({ items: [], next_before: null, active_count: 0, revision: 'base', terminal_revision: 0 });
  vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A', 'B'], thinking_options: {
    A: { default_level: 'low', supported_levels: ['low', 'high'] }, B: { default_level: 'model_default', supported_levels: [] } } });
  vi.spyOn(api, 'sessions').mockResolvedValue([row]);
  vi.spyOn(api, 'session').mockResolvedValue(row);
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: [],
    permission_modes: ['Default'], default_permission_mode: 'Default' });
  vi.spyOn(api, 'context').mockRejectedValue(new Error('no preview'));
  vi.spyOn(api, 'select').mockResolvedValue({ model_id: 'B' });
  vi.spyOn(api, 'selectThinking').mockImplementation(async (_sid, model_id, thinking_level) => ({ model_id, thinking_level }));
  vi.spyOn(api, 'start');
  vi.spyOn(api, 'skills').mockResolvedValue({ entries: [], diagnostics: [], default_source: null, read_registered: true });
  vi.spyOn(api, 'compact').mockResolvedValue({ run_id: 'compact-run', session_id: 's' });
  vi.spyOn(api, 'run').mockResolvedValue({ session_id: 's', run_id: 'compact-run', mode: 'compact', status: 'completed',
    messages: [], last_sequence: 0, incomplete: false, done: true, manual_review_required: false });
  render(<App api={api} />);
  return { api, row };
}

async function selectExisting() {
  fireEvent.click(await screen.findByRole('button', { name: /Invalid Date/ }));
  await waitFor(() => expect((screen.getByRole('button', { name: '查看上下文构成' }) as HTMLButtonElement).disabled).toBe(false));
}

it('places commands before the mode, keeps context beside send and puts models in the header', async () => {
  setup();
  const plus = await screen.findByRole('button', { name: '指令与功能' });
  const mode = screen.getByRole('button', { name: '权限模式：Default' });
  const model = screen.getByRole('button', { name: /模型与推理强度：A/ });
  const context = screen.getByRole('button', { name: '查看上下文构成' });
  const send = screen.getByRole('button', { name: '发送消息' });
  expect(plus.compareDocumentPosition(mode) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(model.closest('header')).not.toBeNull();
  expect(context.compareDocumentPosition(send) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(model.closest('form')).toBeNull();
  expect(context.closest('form')).toBe(send.closest('form'));
  fireEvent.pointerDown(plus);
  expect(screen.getByRole('menuitem', { name: /压缩上下文.*compact/ })).toBeTruthy();
  expect(screen.getAllByRole('menuitem').map(item => item.textContent)).toEqual(['云端任务', '添加图片', '压缩上下文/compact', '技能/skills', '发送后滚动']);
  expect(screen.getByRole('menuitem', { name: '发送后滚动' }).querySelector('[data-chevron="right"]')).not.toBeNull();
  fireEvent.keyDown(screen.getByRole('menu', { name: '指令与功能' }), { key: 'Escape' });
  expect(screen.queryByRole('menu')).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(plus));
});

it('routes compact menu and slash command to the operation and preserves unrelated drafts', async () => {
  const { api, row } = setup();
  await selectExisting();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'keep this task' } });
  fireEvent.pointerDown(screen.getByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: /压缩上下文.*compact/ }));
  await waitFor(() => expect(api.compact).toHaveBeenCalledExactlyOnceWith('s'));
  expect((input as HTMLTextAreaElement).value).toBe('keep this task');
  vi.mocked(api.session).mockResolvedValue(row);
  fireEvent.change(input, { target: { value: '/compact' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(api.compact).toHaveBeenCalledTimes(2));
  await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(''));
  expect(api.start).not.toHaveBeenCalled();
});

it.each(['failed', 'unconfirmed'] as const)('replaces the compact preparation notice when its run is %s', async status => {
  const { api } = setup();
  vi.mocked(api.run).mockResolvedValue({ session_id: 's', run_id: 'compact-run', mode: 'compact', status,
    messages: [], last_sequence: 0, incomplete: status === 'unconfirmed', done: true, manual_review_required: false });
  await selectExisting();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: '保留原任务草稿' } });
  fireEvent.pointerDown(screen.getByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: /压缩上下文.*compact/ }));
  await screen.findByText(status === 'failed' ? '上下文压缩失败，请查看错误详情后重试。' : '压缩结果尚未确认，请同步状态后核对。');
  expect(screen.queryByText('正在准备压缩上下文…')).toBeNull();
  expect((input as HTMLTextAreaElement).value).toBe('保留原任务草稿');
  expect(api.compact).toHaveBeenCalledOnce(); expect(api.start).not.toHaveBeenCalled();
});

it('clears compact preparation once the run is accepted and running', async () => {
  const { api } = setup();
  vi.mocked(api.run).mockResolvedValue({ session_id: 's', run_id: 'compact-run', mode: 'compact', status: 'running',
    messages: [], last_sequence: 0, incomplete: false, done: false, manual_review_required: false });
  vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }));
  });
  await selectExisting();
  fireEvent.pointerDown(screen.getByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: /压缩上下文.*compact/ }));
  await screen.findByText('正在压缩上下文');
  expect(screen.queryByText('正在准备压缩上下文…')).toBeNull();
});

it('only offers supported thinking levels and resets on model change', async () => {
  const { api } = setup();
  await selectExisting();
  fireEvent.click(screen.getByRole('button', { name: /模型与推理强度：A/ }));
  const dialog = screen.getByRole('dialog', { name: '模型与推理强度' });
  expect(within(dialog).queryByRole('radio', { name: 'max' })).toBeNull();
  fireEvent.click(within(screen.getByRole('group', { name: '推理强度' })).getByRole('radio', { name: 'high' }));
  await waitFor(() => expect(api.selectThinking).toHaveBeenCalledExactlyOnceWith('s', 'A', 'high'));
  await screen.findByRole('button', { name: /模型与推理强度：A · high/ });
  fireEvent.click(within(screen.getByRole('group', { name: '模型' })).getByRole('radio', { name: 'B' }));
  await screen.findByRole('button', { name: /模型与推理强度：B · 配置默认/ });
  expect(within(screen.getByRole('group', { name: '推理强度' })).getByRole('radio', { name: '配置默认' }).matches(':disabled')).toBe(true);
});

it('preserves failed compact commands and sends unknown slash text only after explicit submission', async () => {
  const { api } = setup();
  await selectExisting();
  vi.mocked(api.compact).mockRejectedValueOnce(new ApiError('session_busy'));
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: '/compact' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText(/当前会话仍在运行或收尾/);
  expect((input as HTMLTextAreaElement).value).toBe('/compact');
  expect(api.start).not.toHaveBeenCalled();
  vi.mocked(api.start).mockRejectedValueOnce(new ApiError('session_busy'));
  fireEvent.change(input, { target: { value: '/not_supported' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(vi.mocked(api.start).mock.calls[0][1]).toBe('/not_supported');
  expect(api.compact).toHaveBeenCalledOnce();
});


it('retains a background notice until a new chat status replaces it without returning', async () => {
  const { api } = setup();
  await selectExisting();
  await waitFor(() => expect(api.backgroundTasks).toHaveBeenCalled());
  vi.mocked(api.backgroundTasks).mockResolvedValue({ items: [], next_before: null, active_count: 0, revision: 'done', terminal_revision: 1 });
  const poll = async () => {
    act(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(async () => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
  };
  await poll();
  await screen.findByRole('button', { name: '后台任务已结束或需核对' });
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: '新任务' } });
  expect(screen.getByRole('button', { name: '后台任务已结束或需核对' })).toBeTruthy();
  vi.mocked(api.start).mockResolvedValue({ run_id: 'new-run', session_id: 's' });
  vi.mocked(api.run).mockResolvedValue({ session_id: 's', run_id: 'new-run', mode: 'message', status: 'running',
    preparation_phase: 'model', messages: [], last_sequence: 0, incomplete: false, done: false, manual_review_required: false });
  let receive!: Parameters<WebApi['events']>[3];
  vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal, callback) => {
    receive = callback;
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }));
  });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await screen.findByText('正在请求模型');
  expect(screen.queryByRole('button', { name: '后台任务已结束或需核对' })).toBeNull();
  await poll();
  expect(screen.queryByRole('button', { name: '后台任务已结束或需核对' })).toBeNull();
  vi.mocked(api.backgroundTasks).mockResolvedValue({ items: [], next_before: null, active_count: 0, revision: 'later', terminal_revision: 2 });
  await poll();
  await screen.findByRole('button', { name: '后台任务已结束或需核对' });
  expect(screen.queryByText('正在请求模型')).toBeNull();
  await waitFor(() => expect(receive).toBeTypeOf('function'));
  act(() => receive({ event: 'runtime', data: { version: 1, sequence: 1,
    event_type: 'message_delta', session_id: 's', run_id: 'new-run', data: { message_id: 'm', text: 'hello', index: 1 } } }));
  expect(screen.getByRole('button', { name: '后台任务已结束或需核对' })).toBeTruthy();
  act(() => receive({ event: 'runtime', data: { version: 1, sequence: 2,
    event_type: 'run_preparation', session_id: 's', run_id: 'new-run', data: { phase: 'resources' } } }));
  await screen.findByText('正在读取项目指令和 Skills');
  expect(screen.queryByRole('button', { name: '后台任务已结束或需核对' })).toBeNull();
});


it.each([
  ['resources', null, '正在准备压缩所需资料'],
  ['model_request', null, '正在分析历史并生成压缩摘要'],
  ['model_request', { status: 'applied' }, '摘要已应用，正在保存并同步运行结果'],
] as const)('shows the actual compact phase %s instead of a generic wait', async (phase, result, label) => {
  const { api } = setup();
  vi.mocked(api.run).mockResolvedValue({ session_id: 's', run_id: 'compact-run', mode: 'compact', status: 'running',
    preparation_phase: phase, compaction_result: result, messages: [], last_sequence: 0, incomplete: false, done: false, manual_review_required: false });
  vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }));
  });
  await selectExisting();
  fireEvent.pointerDown(screen.getByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: /压缩上下文.*compact/ }));
  await screen.findByText(label);
  expect(screen.getByText(/已等待 \d+ 秒/)).toBeTruthy();
});
