import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { WebApi } from './api';
import type { SseFrame } from './sse';

afterEach(cleanup);

function client() {
  const api = new WebApi('synthetic-test-access');
  const row = { session_id: 's', model_id: 'A', workspace_root: null, state: 'ready',
    updated_at: '2026-10-09T10:00:00Z', last_run_status: null };
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

it('keeps the model selector in the composer without a product title', async () => {
  const { api } = client();
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: /模型与推理强度：A/ }));
  expect(screen.queryByText('Test Agent')).toBeNull();
  expect((screen.getByRole('combobox', { name: '模型' }) as HTMLSelectElement).value).toBe('A');
  expect(screen.getByRole('combobox', { name: '模型' }).closest('header')).toBeNull();
});
