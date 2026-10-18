import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { WebApi } from './api';
import type { SseFrame } from './sse';

afterEach(cleanup);

function client() {
  const api = new WebApi('synthetic-test-access');
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

it('keeps the model selector in the header without a product title', async () => {
  const { api } = client();
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: /模型与推理强度：A/ }));
  expect(screen.queryByText('Test Agent')).toBeNull();
  expect((screen.getByRole('combobox', { name: '模型' }) as HTMLSelectElement).value).toBe('A');
  expect(screen.getByRole('combobox', { name: '模型' }).closest('header')).not.toBeNull();
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

it('hides the child sidebar entry when a session has no live or saved instances', async () => {
  const { api } = client();
  const list = vi.spyOn(api, 'subagents').mockResolvedValue([]);
  render(<App api={api} />);
  expect(screen.queryByRole('button', { name: '打开子智能体侧栏' })).toBeNull();
  fireEvent.click(await screen.findByRole('button', { name: '新建会话' }));
  await waitFor(() => expect(list).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: '打开子智能体侧栏' })).toBeNull();
});

it('shows saved child instances and shares the list with the sidebar without fetching twice', async () => {
  const { api } = client();
  const list = vi.spyOn(api, 'subagents').mockResolvedValue([{ agent_id: 'saved', agent_path: 'root/tester/saved',
    agent: 'tester', task: 'inspect', tool_call_id: 'c', status: 'completed' }]);
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '新建会话' }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
  fireEvent.click(await screen.findByRole('button', { name: '打开子智能体侧栏' }));
  await screen.findByRole('complementary', { name: '子智能体' });
  expect(list).toHaveBeenCalledTimes(1);
  expect(api.start).not.toHaveBeenCalled();
});
