import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { installDialogMock } from './testDialogs';
import { WebApi } from './api';
import type { SseFrame } from './sse';
import type { SessionView } from './types';

afterEach(() => { cleanup(); sessionStorage.clear(); });

it('bridges a verified cloud result into the main draft and sends only after explicit confirmation', async () => {
  const project = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'; const { api } = client(project);
  const row = { ...await api.session('s'), title: '本机主会话' };
  vi.mocked(api.sessions).mockResolvedValue([row]);
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], default_active_tools: [], permission_modes: ['Default'], default_permission_mode: 'Default' });
  vi.spyOn(api, 'context').mockResolvedValue({ session_id: 's', current: { basis: 'unavailable', estimate: null }, latest_request: null });
  const task = { task_id: 'a'.repeat(32), project_id: project, input_id: 'b'.repeat(64), generation: 1, revision: 1, lifecycle: 'ready',
    runner_phase: 'ready', environment_phase: 'backend_ready', active_run_id: null, artifact_operation_id: null, control_operation_id: null,
    environment_operation_id: null, last_quiescent_at: 1, reclaim_blockers: [], runner_model_ids: ['A'] };
  const binding = { binding_id: 'B', local_session_id: 's', task_id: task.task_id, input_id: task.input_id, revision: 1,
    profile_id: 'work', instance_id: 'I', preparation: 'prepared', retained: true, cloud: task };
  const material = { material_id: 'M', receive_id: '', task_id: task.task_id, result_id: 'R', input_id: task.input_id, title: 'report.json', role: 'artifact',
    size: 12, sha256: 'c'.repeat(64), source_run_id: 'd'.repeat(32), outcome: 'failed', captured_at: 1, reported: true };
  const cloud = { projectId: project, profiles: vi.fn().mockResolvedValue([]), list: vi.fn().mockResolvedValue([binding]), binding: vi.fn().mockResolvedValue(binding),
    draft: vi.fn().mockResolvedValue({ text: '', model_id: 'A', draft_revision: 0 }), operations: vi.fn().mockResolvedValue([]),
    models: vi.fn().mockResolvedValue({ model_ids: ['A'], default_model_id: 'A', thinking_options: {} }),
    snapshot: vi.fn().mockResolvedValue({ task, cursor: 'C', archive: { epoch: 'E', messages: [], next_cursor: null }, live: { epoch: 'L', seq: 1, messages: [], tools: [], threads: [] } }),
    usage: vi.fn().mockResolvedValue({ recorded_calls: 0, capture: { capture_incomplete: false } }), threads: vi.fn().mockResolvedValue({ threads: [], next_cursor: null }),
    observe: vi.fn(async (_s, _b, _t, _c, signal: AbortSignal) => { await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true })); }),
    results: vi.fn().mockResolvedValue([{ result_id: 'R', source_run_id: 'run12345', phase: 'available', revision: 1, outcome: 'failed' }]),
    result: vi.fn().mockResolvedValue({ plan: { result_id: 'R', input_id: task.input_id }, phase: 'available', revision: 1, boundary: { outcome: 'failed' }, bundle: { files: [] } }),
    changes: vi.fn().mockResolvedValue({ changes: [], total: 0, next_offset: null }),
    receive: vi.fn(async (_s, _b, _r, body: { command_id: string }) => ({ receive_id: body.command_id, command_id: body.command_id, local_project_id: project,
      local_session_id: 's', binding_id: 'B', result_id: 'R', input_id: task.input_id, phase: 'available', active: false, files: [] })),
    analysisDraft: vi.fn(async (_s, id: string) => ({ local_project_id: project, local_session_id: 's', receive_id: id, text: '请分析云端报告', material_refs: [{ ...material, receive_id: id }] })) };
  vi.spyOn(api, 'cloudTasks').mockReturnValue(cloud as unknown as ReturnType<typeof api.cloudTasks>);
  render(<App api={api} />);
  const sessionButton = await screen.findByRole('button', { name: '本机主会话 A' });
  await act(async () => { fireEvent.click(sessionButton); });
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: '保留的本机请求' } });
  fireEvent.click(screen.getByRole('button', { name: '指令与功能' })); fireEvent.click(screen.getByRole('menuitem', { name: '云端任务' }));
  fireEvent.click(await screen.findByRole('button', { name: /任务 aaaaaaaa/ }));
  const restoreDialog = installDialogMock();
  try {
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  const resultDialog = await screen.findByRole('dialog', { name: /结果与产物/ });
  fireEvent.keyDown(document.body, { key: 'Escape' });
  expect(screen.getByRole('complementary', { name: '辅助侧栏' })).toBeTruthy();
  fireEvent(resultDialog, new Event('cancel', { cancelable: true }));
  fireEvent.click(screen.getByRole('button', { name: /结果与产物/ }));
  fireEvent.click(await screen.findByRole('button', { name: /运行 run12345/ }));
  fireEvent.click(await screen.findByRole('button', { name: '交给主 Agent 分析' }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: '消息' })).toHaveProperty('value', '保留的本机请求\n\n请分析云端报告'));
  expect(screen.getByRole('region', { name: '云端材料引用' })).toBeTruthy(); expect(api.start).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(api.start).toHaveBeenCalledOnce());
  expect(vi.mocked(api.start).mock.calls[0][6]).toEqual(['M']);
  } finally { restoreDialog(); }
});

it('opens cloud observation from the candidate menu without creating a task or main run', async () => {
  const { api } = client();
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '指令与功能' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '云端任务' }));
  expect(screen.getByRole('tab', { name: '云端任务' }).getAttribute('aria-selected')).toBe('true');
  expect(await screen.findByText('请先在已授权的本地项目中选择一个会话，再使用云端任务。')).toBeTruthy();
  expect(api.create).not.toHaveBeenCalled(); expect(api.start).not.toHaveBeenCalled();
});

function client(scopeId?: string) {
  const api = new WebApi('synthetic-test-access', scopeId);
  vi.spyOn(api, 'backgroundTasks').mockResolvedValue({ items: [], active_count: 0, terminal_revision: 0, revision: 'empty', next_before: null });
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  const row = { session_id: 's', model_id: 'A', workspace_root: null, state: 'ready',
    updated_at: '2026-10-09T10:00:00Z', last_run_status: null };
  vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  vi.spyOn(api, 'create').mockResolvedValue({ session_id: 's' });
  vi.spyOn(api, 'session').mockResolvedValue({ ...row, messages: [], busy: false, can_run: true,
    has_compaction: false, active_run_id: null, latest_run_id: null,
    active_tools: [], permission_mode: 'Default', manual_review_required: false, pending_approval: false, live: null });
  vi.spyOn(api, 'start').mockResolvedValue({ session_id: 's', run_id: 'r' });
  vi.spyOn(api, 'run').mockResolvedValue({ session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false });
  let receive: (frame: SseFrame) => void = () => { throw new Error('not subscribed'); };
  vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal, sink) => {
    receive = sink;
    sink({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'r',
      sequence: 1, event_type: 'run_started', data: {} } });
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve()));
  });
  return { api, emit: (frame: SseFrame) => receive(frame) };
}

it.each([
  ['missing', '目录不存在'],
  ['unreadable', '无法访问或检查执行目录'],
] as const)('keeps history visible for execution directory reason %s', async (reason, explanation) => {
  const { api } = client();
  const original: SessionView = { ...await api.session('s'), title: '已保存会话', messages: [
    { id: 'saved', type: 'ai', content: '历史回答', provisional: false, state: 'complete' }
  ] };
  vi.mocked(api.sessions).mockResolvedValue([original]);
  vi.mocked(api.session).mockResolvedValue({ ...original, can_run: false,
    execution_root_error: { code: 'execution_root_unavailable', reason } });
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '已保存会话 A' }));
  expect(await screen.findByText('历史回答')).toBeTruthy();
  expect(await screen.findByText(`执行目录不可用：${explanation}。历史仍可查看。`)).toBeTruthy();
  expect((screen.getByRole('button', { name: '发送消息' }) as HTMLButtonElement).disabled).toBe(true);
  expect(api.start).not.toHaveBeenCalled();
});

it('opens the shared sidebar, selects tasks, and retains it when creating a chat', async () => {
  const { api } = client();
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '打开辅助侧栏' }));
  fireEvent.click(screen.getByRole('tab', { name: /后台任务/ }));
  expect(await screen.findByText('没有后台任务')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /新建会话/ }));
  expect(api.create).not.toHaveBeenCalled();
  expect(screen.getByRole('complementary', { name: '辅助侧栏' })).toBeTruthy();
  expect(api.start).not.toHaveBeenCalled();
});

it('uses one icon and four keyboard tabs, then returns focus when collapsed', async () => {
  const { api } = client();
  render(<App api={api} />);
  const trigger = await screen.findByRole('button', { name: '打开辅助侧栏' });
  expect(trigger.querySelector('svg')).toBeTruthy();
  fireEvent.click(trigger);
  expect(screen.queryByText('会话辅助')).toBeNull();
  const sidebar = screen.getByRole('complementary', { name: '辅助侧栏' });
  const tabs = within(sidebar).getByRole('tablist', { name: '辅助功能' });
  const close = within(sidebar).getByRole('button', { name: '收起辅助侧栏' });
  expect(tabs.closest('header')).toBe(close.closest('header'));
  expect(sidebar.firstElementChild).toBe(tabs.closest('header'));
  const agents = screen.getByRole('tab', { name: '子智能体' });
  expect(agents.getAttribute('aria-selected')).toBe('true');
  fireEvent.keyDown(agents, { key: 'ArrowRight' });
  expect(screen.getByRole('tab', { name: /后台任务/ }).getAttribute('aria-selected')).toBe('true');
  fireEvent.keyDown(document.activeElement!, { key: 'End' });
  expect(screen.getByRole('tab', { name: '云端任务' }).getAttribute('aria-selected')).toBe('true');
  fireEvent.keyDown(screen.getByRole('complementary', { name: '辅助侧栏' }), { key: 'Escape' });
  expect(screen.queryByRole('complementary', { name: '辅助侧栏' })).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(api.start).not.toHaveBeenCalled();
});

it('pauses selected task output on another tab and preserves its selection', async () => {
  const { api } = client();
  const row = { task_id: 'a'.repeat(32), session_id: 's', workspace_root: '/workspace', origin_run_id: 'r',
    tool_call_id: 'c', shell: 'bash', command_preview: 'service command', status: 'running' as const,
    created_at: '', started_at: null, finished_at: null, max_execution_seconds: null, exit_code: null,
    termination: null, output_complete: false, cleanup_confirmed: false, error_type: null };
  vi.mocked(api.backgroundTasks).mockResolvedValue({ items: [row], active_count: 1, revision: 'one', terminal_revision: 0, next_before: null });
  const output = { ...row, output: 'CURRENT', truncated: false, observed_raw_bytes: 7, observed_text_bytes: 7,
    lossy: false, archive_paths: {}, archive_saved_bytes: 0, archive_complete: false, archive_reason: null, output_available: true };
  let finish!: (value: typeof output) => void;
  const read = vi.spyOn(api, 'backgroundTaskOutput').mockImplementationOnce(() => new Promise(done => { finish = done; }))
    .mockResolvedValue(output);
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '打开辅助侧栏' }));
  fireEvent.click(screen.getByRole('tab', { name: /后台任务/ }));
  fireEvent.click(await screen.findByRole('button', { name: /service command/ }));
  await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  const signal = read.mock.calls[0][1]!;
  fireEvent.click(screen.getByRole('tab', { name: '上下文' }));
  expect(signal.aborted).toBe(true);
  fireEvent.click(screen.getByRole('tab', { name: /后台任务/ }));
  await screen.findByText('CURRENT');
  await act(async () => finish({ ...output, output: 'STALE' }));
  expect(screen.queryByText('STALE')).toBeNull();
  expect(screen.getByText('CURRENT')).toBeTruthy();
  expect(api.start).not.toHaveBeenCalled();
});

it('collapses with Escape after the context shortcut jumps into an already open sidebar', async () => {
  const { api } = client();
  vi.mocked(api.sessions).mockResolvedValue([{ ...await api.session('s'), title: '已有会话' }]);
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '已有会话 A' }));
  await waitFor(() => expect(api.prepare).toHaveBeenCalledWith('s'));
  fireEvent.click(screen.getByRole('button', { name: '打开辅助侧栏' }));
  const context = screen.getByRole('button', { name: '查看上下文构成' });
  fireEvent.click(context);
  expect(screen.getByRole('tab', { name: '上下文' }).getAttribute('aria-selected')).toBe('true');
  context.focus();
  fireEvent.keyDown(context, { key: 'Escape' });
  expect(screen.queryByRole('complementary', { name: '辅助侧栏' })).toBeNull();
  expect(document.activeElement).toBe(context);
});

it('renders an empty conversation, then sends a real input and keeps editor available', async () => {
  const { api } = client();
  vi.spyOn(api, 'control').mockResolvedValue({ accepted: true, retained: [] });
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  expect(screen.getByText('还没有历史会话')).toBeTruthy();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'first' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(api.events).toHaveBeenCalled());
  expect((input as HTMLTextAreaElement).disabled).toBe(false);
  fireEvent.change(input, { target: { value: 'steering' } });
  fireEvent.change(screen.getByRole('combobox', { name: '发送方式' }), { target: { value: 'steering' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(api.control).toHaveBeenCalledWith('r', 'steering', 'steering'));
});

it('retains input during stop, without automatically starting another run', async () => {
  const { api, emit } = client();
  let finishStop!: (value: { accepted: boolean; retained: string[] }) => void;
  vi.spyOn(api, 'control').mockImplementation(() => new Promise(resolve => { finishStop = resolve; }));
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'first' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(api.events).toHaveBeenCalled());
  fireEvent.click(screen.getByRole('button', { name: '停止运行' }));
  fireEvent.change(input, { target: { value: 'new while stopping' } });
  await act(async () => finishStop({ accepted: true, retained: ['unused follow-up'] }));
  await act(async () => emit({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'r', sequence: 2,
    event_type: 'run_finished', data: { status: 'stopped', manual_review_required: false } } }));
  expect((input as HTMLTextAreaElement).value).toBe('new while stopping');
  expect(screen.getByDisplayValue('unused follow-up')).toBeTruthy();
  expect(api.start).toHaveBeenCalledTimes(1);
});

it('puts project selection above the message without the retired execution menu', async () => {
  const { api } = client('11111111-1111-1111-1111-111111111111');
  render(<App api={api} projectEntry={() => <button>项目选择入口</button>} />);
  const context = await screen.findByRole('region', { name: '项目与执行位置' });
  const project = within(context).getByRole('button', { name: '项目选择入口' });
  expect(within(context).queryByRole('button', { name: /执行位置/ })).toBeNull();
  expect(project.closest('header')).toBeNull();
  expect(context.parentElement?.contains(screen.getByRole('textbox', { name: '消息' }))).toBe(true);
  expect(context.compareDocumentPosition(screen.getByRole('textbox', { name: '消息' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('keeps the model selector in the header without a product title', async () => {
  const { api } = client();
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: /模型与推理强度：A/ }));
  expect(screen.queryByText('Test Agent')).toBeNull();
  expect((within(screen.getByRole('group', { name: '模型' })).getByRole('radio', { name: 'A' }) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole('group', { name: '模型' }).closest('header')).not.toBeNull();
});

it('opens the same header model menu with /model without starting a run', async () => {
  const { api } = client();
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: '/model' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  expect(screen.getByRole('dialog', { name: '模型与推理强度' }).closest('header')).not.toBeNull();
  expect(screen.getAllByRole('button', { name: /模型与推理强度：A/ })).toHaveLength(1);
  expect(api.start).not.toHaveBeenCalled();
});

it('clears normal completion status and the resync button after a confirmed finish', async () => {
  const { api, emit } = client();
  render(<App api={api} />);
  await screen.findByRole('button', { name: /模型与推理强度：A/ });
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'finish normally' } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(api.events).toHaveBeenCalled());
  vi.mocked(api.run).mockResolvedValue({ session_id: 's', run_id: 'r', status: 'completed', messages: [],
    last_sequence: 2, incomplete: false, done: true, manual_review_required: false });
  await act(async () => emit({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'r', sequence: 2,
    event_type: 'run_finished', data: { status: 'completed' } } }));
  await screen.findByRole('button', { name: '发送消息' });
  expect(screen.queryByText('本次运行完成')).toBeNull();
  expect(screen.queryByRole('button', { name: '重新同步' })).toBeNull();
});

it('keeps the shared entry available with an empty child view', async () => {
  const { api } = client();
  const list = vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.mocked(api.sessions).mockResolvedValue([{ ...await api.session('s'), title: '已有会话' }]);
  render(<App api={api} />);
  expect(screen.getByRole('button', { name: '打开辅助侧栏' })).toBeTruthy();
  fireEvent.click(await screen.findByRole('button', { name: '已有会话 A' }));
  await waitFor(() => expect(list).toHaveBeenCalled());
  expect(screen.getByRole('button', { name: '打开辅助侧栏' })).toBeTruthy();
});

it('shows saved child instances and shares the list with the sidebar without fetching twice', async () => {
  const { api } = client();
  const list = vi.spyOn(api, 'subagents').mockResolvedValue([{ agent_id: 'saved', agent_path: 'root/tester/saved',
    agent: 'tester', task: 'inspect', tool_call_id: 'c', status: 'completed' }]);
  vi.mocked(api.sessions).mockResolvedValue([{ ...await api.session('s'), title: '已有会话' }]);
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '已有会话 A' }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
  fireEvent.click(await screen.findByRole('button', { name: '打开辅助侧栏' }));
  await screen.findByRole('region', { name: '子智能体' });
  expect(list).toHaveBeenCalledTimes(1);
  expect(api.start).not.toHaveBeenCalled();
});

it('lets both sidebars use only the main conversation’s spare width', async () => {
  const { api } = client();
  const { container } = render(<App api={api} />);
  await waitFor(() => expect(api.sessions).toHaveBeenCalled());
  const layout = container.firstElementChild as HTMLElement;
  const workspace = screen.getByRole('main');
  const leftPane = screen.getByRole('complementary', { name: '会话导航' });
  layout.style.setProperty('--navigation-width', '260px');
  layout.style.setProperty('--activity-width', '440px');
  layout.style.setProperty('--conversation-width', '768px');
  layout.style.setProperty('--conversation-padding-x', '28px');
  layout.style.setProperty('--conversation-scrollbar', '16px');
  const extra = (side: string) => parseFloat(layout.style.getPropertyValue(`--${side}-requested`)) || 0;
  Object.defineProperty(workspace, 'clientWidth', { get: () => 940 - extra('navigation') - extra('activity') });
  Object.defineProperty(leftPane, 'offsetWidth', { get: () => 260 + extra('navigation') });

  const left = screen.getByRole('separator', { name: '调整会话导航宽度' });
  fireEvent.pointerDown(left, { pointerId: 1, clientX: 260, button: 0 });
  fireEvent.pointerMove(left, { pointerId: 1, clientX: 300 });
  fireEvent.pointerUp(left, { pointerId: 1 });
  expect(extra('navigation')).toBe(40);

  fireEvent.click(screen.getByRole('button', { name: '打开辅助侧栏' }));
  const rightPane = screen.getByRole('complementary', { name: '辅助侧栏' });
  Object.defineProperty(rightPane, 'offsetWidth', { get: () => 440 + extra('activity') });
  const right = screen.getByRole('separator', { name: '调整辅助侧栏宽度' });
  fireEvent.pointerDown(right, { pointerId: 2, clientX: 1200, button: 0 });
  fireEvent.pointerMove(right, { pointerId: 2, clientX: 1000 });
  fireEvent.pointerUp(right, { pointerId: 2 });
  expect(extra('activity')).toBe(60);
  fireEvent.keyDown(left, { key: 'End' });
  expect(extra('navigation')).toBe(40);
  fireEvent.keyDown(right, { key: 'ArrowRight' });
  expect(extra('activity')).toBe(44);
  fireEvent.keyDown(right, { key: 'Home' });
  expect(extra('activity')).toBe(0);
  fireEvent.keyDown(left, { key: 'End' });
  expect(extra('navigation')).toBe(100);
  fireEvent.click(screen.getByRole('button', { name: '收起侧栏' }));
  expect(screen.queryByRole('separator', { name: '调整会话导航宽度' })).toBeNull();
});
