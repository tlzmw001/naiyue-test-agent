import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  await waitFor(() => expect((screen.getByRole('button', { name: '打开子智能体侧栏' }) as HTMLButtonElement).disabled).toBe(false));
}

it('places commands before the mode and models between context and send', async () => {
  setup();
  const plus = await screen.findByRole('button', { name: '指令与功能' });
  const mode = screen.getByRole('button', { name: '权限模式：Default' });
  const model = screen.getByRole('button', { name: /模型与推理强度：A/ });
  const context = screen.getByRole('button', { name: '查看上下文构成' });
  const send = screen.getByRole('button', { name: '发送消息' });
  expect(plus.compareDocumentPosition(mode) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(context.compareDocumentPosition(model) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(model.compareDocumentPosition(send) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(model.closest('form')).toBe(send.closest('form'));
  fireEvent.click(plus);
  expect(screen.getByRole('menuitem', { name: /压缩上下文.*compact/ })).toBeTruthy();
  expect(screen.getAllByRole('menuitem').map(item => item.textContent)).toEqual(['压缩上下文/compact', '技能/skills']);
  fireEvent.keyDown(screen.getByRole('menu', { name: '指令与功能' }), { key: 'Escape' });
  expect(screen.queryByRole('menu')).toBeNull();
  expect(document.activeElement).toBe(plus);
});

it('routes compact menu and slash command to the operation and preserves unrelated drafts', async () => {
  const { api, row } = setup();
  await selectExisting();
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: 'keep this task' } });
  fireEvent.click(screen.getByRole('button', { name: '指令与功能' }));
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

it('only offers supported thinking levels and resets on model change', async () => {
  const { api } = setup();
  await selectExisting();
  fireEvent.click(screen.getByRole('button', { name: /模型与推理强度：A/ }));
  const dialog = screen.getByRole('dialog', { name: '模型与推理强度' });
  expect(within(dialog).queryByRole('option', { name: 'max' })).toBeNull();
  fireEvent.change(within(dialog).getByRole('combobox', { name: '推理强度' }), { target: { value: 'high' } });
  await waitFor(() => expect(api.selectThinking).toHaveBeenCalledExactlyOnceWith('s', 'A', 'high'));
  await screen.findByRole('button', { name: /模型与推理强度：A · high/ });
  fireEvent.change(screen.getByRole('combobox', { name: '模型' }), { target: { value: 'B' } });
  await screen.findByRole('button', { name: /模型与推理强度：B · 配置默认/ });
  expect((screen.getByRole('combobox', { name: '推理强度' }) as HTMLSelectElement).disabled).toBe(true);
});

it('keeps failed compact commands and unknown commands out of the model', async () => {
  const { api } = setup();
  await selectExisting();
  vi.mocked(api.compact).mockRejectedValueOnce(new ApiError('session_busy'));
  const input = screen.getByRole('textbox', { name: '消息' });
  fireEvent.change(input, { target: { value: '/compact' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText(/当前会话仍在运行或收尾/);
  expect((input as HTMLTextAreaElement).value).toBe('/compact');
  fireEvent.change(input, { target: { value: '/not_supported' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await screen.findByText(/不支持的指令/);
  expect(api.start).not.toHaveBeenCalled();
});
