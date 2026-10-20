import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import ComposerControls from './components/ComposerControls';
import { ApiError, WebApi } from './api';
import type { PermissionMode, RunView, SessionView } from './types';

afterEach(cleanup);

it('shows each selected mode as its name and chevron without a status circle', () => {
  const modes: PermissionMode[] = ['Default', 'NoAsk', 'Auto', 'BypassApprovals'];
  const props = { options: null, tools: [], disabled: false, loading: false, error: false,
    reload: vi.fn(), selectMode: vi.fn(), selectTools: vi.fn(), sessionId: null, menu: null,
    setMenu: vi.fn(), runCommand: vi.fn(), compactDisabled: true,
    api: new WebApi('fixture'), selectSkill: vi.fn(), focusEditor: vi.fn() };
  const { rerender } = render(<ComposerControls {...props} mode="Default" />);
  for (const mode of modes) {
    rerender(<ComposerControls {...props} mode={mode} />);
    const button = screen.getByRole('button', { name: `权限模式：${mode}` });
    expect(button.textContent).toBe(`${mode} ⌃`);
    expect(Array.from(button.children, child => child.textContent)).toEqual(['⌃']);
  }
});

function setup() {
  const api = new WebApi('synthetic-test-access');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  const session: SessionView = { session_id: 's', model_id: 'A', workspace_root: '/workspace', state: 'ready',
    updated_at: '', last_run_status: null, messages: [], busy: false, can_run: true, has_compaction: false,
    active_run_id: null, latest_run_id: null, active_tools: ['read'], permission_mode: 'Default',
    manual_review_required: false, pending_approval: false, live: null };
  vi.spyOn(api, 'subagents').mockResolvedValue([]);
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([session]);
  vi.spyOn(api, 'session').mockResolvedValue(session);
  vi.spyOn(api, 'create').mockResolvedValue({ session_id: 's' });
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ default_active_tools: ['read'],
    default_permission_mode: 'Default', permission_modes: ['Default', 'NoAsk', 'BypassApprovals'],
    tools: [{ name: 'read', description: '读取文件' }, { name: 'bash', description: '运行命令' }] });
  vi.spyOn(api, 'selectPermissionMode').mockImplementation(async (_id, permission_mode) => ({ permission_mode }));
  vi.spyOn(api, 'selectTools').mockImplementation(async (_id, active_tools) => ({ active_tools }));
  vi.spyOn(api, 'start');
  render(<App api={api} />);
  return { api, session };
}

it('keeps mode and tools inside the composer, and creates a session without sending the draft', async () => {
  const { api } = setup();
  const mode = await screen.findByRole('button', { name: '权限模式：Default' });
  expect(mode.closest('form')).toBe(screen.getByRole('textbox', { name: '消息' }).closest('form'));
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'keep this draft' } });
  fireEvent.click(mode);
  expect(screen.getByText('未配置 Auto 审查模型')).toBeTruthy();
  fireEvent.click(screen.getByRole('radio', { name: /NoAsk/ }));
  await screen.findByRole('button', { name: '权限模式：NoAsk' });
  expect(api.selectPermissionMode).toHaveBeenCalledExactlyOnceWith('s', 'NoAsk');
  expect(api.create).toHaveBeenCalledExactlyOnceWith('A');
  expect((input as HTMLTextAreaElement).value).toBe('keep this draft');
  expect(api.start).not.toHaveBeenCalled();
});

it('selects the registered tools for the current main agent and preserves a failed selection', async () => {
  const { api } = setup();
  fireEvent.click(await screen.findByRole('button', { name: /工具.*1/ }));
  expect(screen.getByText('下一次模型调用生效。')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox', { name: /bash/ }));
  await waitFor(() => expect(api.selectTools).toHaveBeenCalledExactlyOnceWith('s', ['read', 'bash']));
  await waitFor(() => expect((screen.getByRole('checkbox', { name: /bash/ }) as HTMLInputElement).checked).toBe(true));
  expect(screen.queryByText(/工具选择已保存/)).toBeNull();
  vi.mocked(api.selectTools).mockRejectedValueOnce(new ApiError('invalid_tool_selection'));
  fireEvent.click(screen.getByRole('checkbox', { name: /read/ }));
  await screen.findByText(/工具选择无效/);
  expect((screen.getByRole('checkbox', { name: /read/ }) as HTMLInputElement).checked).toBe(true);
  expect(api.selectTools).toHaveBeenCalledTimes(2);
  expect(api.start).not.toHaveBeenCalled();
});

it('cycles modes only from the message editor and dismisses the menu with Escape', async () => {
  const { api } = setup();
  const mode = await screen.findByRole('button', { name: '权限模式：Default' });
  fireEvent.keyDown(mode, { key: 'Tab', shiftKey: true });
  expect(api.selectPermissionMode).not.toHaveBeenCalled();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.keyDown(input, { key: 'Tab', shiftKey: true, isComposing: true });
  expect(api.selectPermissionMode).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: 'Tab', shiftKey: true });
  await screen.findByRole('button', { name: '权限模式：NoAsk' });
  fireEvent.click(screen.getByRole('button', { name: '权限模式：NoAsk' }));
  const dialog = screen.getByRole('dialog', { name: '权限模式' });
  expect(document.activeElement).toBe(within(dialog).getByRole('radio', { name: /NoAsk/ }));
  fireEvent.keyDown(within(dialog).getByRole('radio', { name: /NoAsk/ }), { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: '权限模式' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /工具.*1/ }));
  expect(screen.getByRole('dialog', { name: '主 Agent 工具' })).toBeTruthy();
  fireEvent.pointerDown(input);
  expect(screen.queryByRole('dialog', { name: '主 Agent 工具' })).toBeNull();
});

it('shows an option loading error and retries without creating a session', async () => {
  const { api } = setup();
  vi.mocked(api.sessionControlOptions).mockRejectedValueOnce(new Error('unavailable'));
  // The first load is already in flight; explicitly reopen the failed query below.
  await screen.findByRole('button', { name: '权限模式：Default' });
  fireEvent.click(screen.getByRole('button', { name: '权限模式：Default' }));
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  await screen.findByText(/工具与模式加载失败/);
  fireEvent.click(screen.getByRole('button', { name: '重试加载' }));
  await screen.findByRole('radio', { name: /Default/ });
  expect(api.create).not.toHaveBeenCalled();
});

it('reports a failed first session read, keeps its identity and draft, and retries without another create', async () => {
  const { api, session } = setup();
  vi.mocked(api.session).mockRejectedValueOnce(new Error('first read failed'))
    .mockRejectedValueOnce(new Error('confirmation read failed')).mockResolvedValue(session);
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'keep my draft' } });
  fireEvent.click(await screen.findByRole('button', { name: '权限模式：Default' }));
  fireEvent.click(screen.getByRole('radio', { name: /NoAsk/ }));
  await screen.findByText(/当前选择刷新未确认/);
  expect((screen.getByRole('textbox', { name: '消息' }) as HTMLTextAreaElement).value).toBe('keep my draft');
  fireEvent.click(screen.getByRole('button', { name: '权限模式：Default' }));
  fireEvent.click(screen.getByRole('radio', { name: /NoAsk/ }));
  await screen.findByRole('button', { name: '权限模式：NoAsk' });
  expect(api.create).toHaveBeenCalledTimes(1);
  expect(api.selectPermissionMode).toHaveBeenCalledExactlyOnceWith('s', 'NoAsk');
  expect(api.start).not.toHaveBeenCalled();
});

it('keeps confirmed choices when an older run refresh returns after saving', async () => {
  const { api, session } = setup();
  const live: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
  vi.mocked(api.session).mockResolvedValue({ ...session, busy: true, live });
  vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve()));
  });
  vi.spyOn(api, 'run').mockResolvedValue({ ...live, status: 'completed', done: true });
  fireEvent.click(await screen.findByRole('button', { name: /Invalid Date/ }));
  await screen.findByRole('button', { name: '重新同步' });
  let finish!: (value: SessionView) => void;
  vi.mocked(api.session).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValue({ ...session, busy: true, live });
  fireEvent.click(screen.getByRole('button', { name: '重新同步' }));
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  fireEvent.click(screen.getByRole('button', { name: '权限模式：Default' }));
  fireEvent.click(screen.getByRole('radio', { name: /NoAsk/ }));
  await screen.findByRole('button', { name: '权限模式：NoAsk' });
  fireEvent.click(screen.getByRole('button', { name: /工具.*1/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /bash/ }));
  await waitFor(() => expect(api.selectTools).toHaveBeenCalledExactlyOnceWith('s', ['read', 'bash']));
  // Reconnect now prepares first, then reads terminal state. Keep both reads
  // independently pending so a later confirmation cannot hide an old-response regression.
  let finishLatest!: (value: SessionView) => void;
  vi.mocked(api.session).mockImplementationOnce(() => new Promise(resolve => { finishLatest = resolve; }));
  await act(async () => finish(session));
  await waitFor(() => expect(finishLatest).toBeTypeOf('function'));
  expect(screen.getByRole('button', { name: '权限模式：NoAsk' })).toBeTruthy();
  expect((screen.getByRole('checkbox', { name: /bash/ }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: /read/ }));
  await waitFor(() => expect(api.selectTools).toHaveBeenLastCalledWith('s', ['bash']));
  await act(async () => finishLatest({ ...session, permission_mode: 'NoAsk', active_tools: ['read', 'bash'] }));
  expect((screen.getByRole('checkbox', { name: /read/ }) as HTMLInputElement).checked).toBe(false);
});

it.each(['follow_up', 'steering'] as const)('uses %s for a running main agent without starting a new run', async kind => {
  const { api, session } = setup();
  const live: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
    last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
  vi.mocked(api.session).mockResolvedValue({ ...session, busy: true, live });
  vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve()));
  });
  vi.spyOn(api, 'control').mockResolvedValue({ accepted: true, retained: [] });
  fireEvent.click(await screen.findByRole('button', { name: /Invalid Date/ }));
  await screen.findByRole('button', { name: '停止运行' });
  expect(screen.queryByRole('combobox', { name: '发送方式' })).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: '消息' }), { target: { value: 'next task' } });
  const selector = screen.getByRole('combobox', { name: '发送方式' });
  expect((selector as HTMLSelectElement).value).toBe('follow_up');
  if (kind === 'steering') fireEvent.change(selector, { target: { value: kind } });
  fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
  await waitFor(() => expect(api.control).toHaveBeenCalledExactlyOnceWith('r', kind, 'next task'));
  expect(api.start).not.toHaveBeenCalled();
});

it('refreshes MCP before loading the new tools through the existing refresh button', async () => {
  const { api } = setup();
  let finish!: (value: { servers: [] }) => void;
  const refresh = vi.spyOn(api, 'refreshMcp').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  fireEvent.click(await screen.findByRole('button', { name: /工具.*1/ }));
  expect(screen.queryByRole('button', { name: '刷新 MCP 配置' })).toBeNull();
  expect(refresh).not.toHaveBeenCalled();
  expect(api.sessionControlOptions).toHaveBeenCalledOnce();
  const old = await vi.mocked(api.sessionControlOptions).mock.results[0].value;
  vi.mocked(api.sessionControlOptions).mockResolvedValue({ ...old,
    tools: [...old.tools, { name: 'mcp__local__echo', description: 'external' }] });
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  expect(refresh).toHaveBeenCalledOnce();
  expect(api.sessionControlOptions).toHaveBeenCalledOnce();
  finish({ servers: [] });
  const checkbox = await screen.findByRole('checkbox', { name: /mcp__local__echo/ }) as HTMLInputElement;
  expect(checkbox.checked).toBe(false);
  expect(api.sessionControlOptions).toHaveBeenCalledTimes(2);
  expect(api.create).not.toHaveBeenCalled();
  expect(api.selectTools).not.toHaveBeenCalled();
  expect(api.start).not.toHaveBeenCalled();
  expect(screen.queryByText('MCP 配置刷新完成')).toBeNull();
});

it('still reloads effective tool options when MCP configuration refresh fails', async () => {
  const { api } = setup();
  vi.spyOn(api, 'refreshMcp').mockRejectedValue(new Error('PRIVATE'));
  fireEvent.click(await screen.findByRole('button', { name: /工具.*1/ }));
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  await screen.findByText(/MCP 配置刷新失败/);
  await waitFor(() => expect(api.sessionControlOptions).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole('checkbox', { name: /read/ })).toBeTruthy();
  expect(screen.queryByText('PRIVATE')).toBeNull();
  expect(api.create).not.toHaveBeenCalled();
  expect(api.start).not.toHaveBeenCalled();
});
