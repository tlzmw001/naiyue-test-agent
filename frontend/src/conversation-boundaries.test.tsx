import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import { useConversation } from './useConversation';
import type { RunView, SessionView } from './types';

afterEach(cleanup);
const live: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
  last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
function session(id: string): SessionView {
  return { session_id: id, model_id: 'A', workspace_root: null, state: 'ready',
    updated_at: '', last_run_status: null, messages: [], busy: false, can_run: true,
    has_compaction: false, active_run_id: null, latest_run_id: null,
    active_tools: [], permission_mode: 'Default', manual_review_required: false, pending_approval: false, live: null };
}
function setup() {
  const api = new WebApi('synthetic-test-access');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A', 'B'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve()));
  });
  vi.spyOn(api, 'run').mockResolvedValue(live);
  const hook = renderHook(() => useConversation(api));
  return { api, ...hook };
}
it('ignores an older session response after a newer selection', async () => {
  const { api, result } = setup();
  await waitFor(() => expect(result.current.model).toBe('A'));
  let resolveOld!: (value: SessionView) => void;
  vi.spyOn(api, 'session').mockImplementation(id => id === 'old' ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(session(id)));
  let old!: Promise<void>;
  act(() => { old = result.current.selectSession('old'); });
  await act(async () => result.current.selectSession('newer'));
  await act(async () => { resolveOld(session('old')); await old; });
  expect(result.current.session?.session_id).toBe('newer');
});
it('restores retained messages without automatically sending them', async () => {
  const { api, result } = setup();
  vi.spyOn(api, 'session').mockResolvedValue({ ...session('s'), live: {
    ...live, status: 'stopped', done: true, retained: ['not delivered'] } } as SessionView);
  const start = vi.spyOn(api, 'start');
  await act(async () => result.current.selectSession('s'));
  expect(result.current.pending).toEqual(['not delivered']);
  expect(result.current.pendingMode).toBe(true);
  expect(start).not.toHaveBeenCalled();
});
it('does not display waiting for cleanup when the stop receipt arrives after the terminal', async () => {
  const { api, result } = setup();
  await waitFor(() => expect(result.current.model).toBe('A'));
  vi.spyOn(api, 'session').mockResolvedValue({ ...session('s'), live });
  await act(async () => result.current.selectSession('s'));
  let resolveStop!: (value: { accepted: boolean; retained: string[] }) => void;
  vi.spyOn(api, 'control').mockImplementation(() => new Promise(resolve => { resolveStop = resolve; }));
  let stopped!: Promise<void>;
  act(() => { stopped = result.current.stop(); });
  vi.mocked(api.run).mockResolvedValue({ ...live, status: 'stopped', done: true });
  await act(async () => result.current.reconnect());
  await waitFor(() => expect(result.current.run?.status).toBe('stopped'));
  await act(async () => { resolveStop({ accepted: true, retained: [] }); await stopped; });
  expect(result.current.notice).not.toContain('正在等待');
});

it('preserves independent drafts and keeps the confirmed model when a switch fails', async () => {
  const { api, result } = setup();
  vi.spyOn(api, 'session').mockImplementation(id => Promise.resolve(session(id)));
  vi.spyOn(api, 'select').mockRejectedValue(new Error('synthetic failure'));
  await act(async () => result.current.selectSession('one'));
  act(() => result.current.setDraft('draft one'));
  await act(async () => result.current.selectSession('two'));
  act(() => result.current.setDraft('draft two'));
  await act(async () => result.current.selectSession('one'));
  expect(result.current.draft).toBe('draft one');
  await act(async () => result.current.selectModel('B'));
  expect(result.current.model).toBe('A');
  expect(result.current.notice).toContain('操作未确认');
});

it('reads back a model selection when the save succeeded but its receipt was lost', async () => {
  const { api, result } = setup();
  vi.spyOn(api, 'session').mockResolvedValueOnce(session('one')).mockResolvedValue({ ...session('one'), model_id: 'B', thinking_level: 'model_default' });
  vi.spyOn(api, 'select').mockRejectedValue(new Error('receipt lost'));
  await act(async () => result.current.selectSession('one'));
  await act(async () => result.current.selectModel('B'));
  expect(result.current.model).toBe('B');
  expect(result.current.session?.model_id).toBe('B');
  expect(result.current.notice).toContain('操作未确认');
});

it('keeps a confirmed failure readback when an older run refresh returns last', async () => {
  const { api, result } = setup();
  vi.spyOn(api, 'session').mockResolvedValue({ ...session('s'), live });
  await act(async () => result.current.selectSession('s'));
  let rejectPatch!: (error: Error) => void;
  vi.spyOn(api, 'select').mockImplementation(() => new Promise((_resolve, reject) => { rejectPatch = reject; }));
  let switching!: Promise<void>;
  act(() => { switching = result.current.selectModel('B'); });
  let oldRead!: (value: SessionView) => void;
  vi.mocked(api.run).mockResolvedValue({ ...live, status: 'completed', done: true });
  vi.mocked(api.session).mockImplementationOnce(() => new Promise(resolve => { oldRead = resolve; }))
    .mockResolvedValue({ ...session('s'), model_id: 'B', thinking_level: 'model_default' });
  act(() => result.current.reconnect());
  await waitFor(() => expect(oldRead).toBeTypeOf('function'));
  await act(async () => { rejectPatch(new Error('receipt lost')); await switching; });
  expect(result.current.session?.model_id).toBe('B');
  await act(async () => oldRead({ ...session('s'), model_id: 'A', thinking_level: 'high' }));
  expect(result.current.model).toBe('B');
  expect(result.current.session?.model_id).toBe('B');
  expect(result.current.session?.thinking_level).toBe('model_default');
});
it('retains text after a failed start and never automatically retries the POST', async () => {
  const { api, result } = setup();
  vi.spyOn(api, 'session').mockResolvedValue(session('s'));
  vi.spyOn(api, 'start').mockRejectedValue(new Error('connection unavailable'));
  await act(async () => result.current.selectSession('s'));
  act(() => result.current.setDraft('keep my message'));
  await act(async () => result.current.submit(false));
  expect(result.current.draft).toBe('keep my message');
  expect(api.start).toHaveBeenCalledTimes(1);
});

it('does not apply an old model-switch acknowledgement to another selected session', async () => {
  const { api, result } = setup();
  vi.spyOn(api, 'session').mockImplementation(id => Promise.resolve({ ...session(id), model_id: id === 'two' ? 'B' : 'A' }));
  let finish!: () => void;
  vi.spyOn(api, 'select').mockImplementation(() => new Promise(resolve => { finish = () => resolve({ model_id: 'B' }); }));
  await act(async () => result.current.selectSession('one'));
  let switched!: Promise<void>;
  act(() => { switched = result.current.selectModel('B'); });
  await act(async () => result.current.selectSession('two'));
  await act(async () => { finish(); await switched; });
  expect(result.current.session?.session_id).toBe('two');
  expect(result.current.notice).not.toContain('模型已切换');
});

it('restores progress and accepted stopping from a reconnect snapshot', async () => {
  const { api, result } = setup();
  const progress = { tool_call_id: 't', tool_name: 'bash', run_id: 'r', batch_id: 'ai',
    state: 'running', boundary: 'dispatch', phase: 'process_output', text: 'RESTORED_TAIL' } as const;
  vi.spyOn(api, 'session').mockResolvedValue({ ...session('s'), live });
  await act(async () => result.current.selectSession('s'));
  vi.mocked(api.run).mockResolvedValue({ ...live, last_sequence: 20, stop_requested: true,
    tool_states: { t: progress } });
  await act(async () => result.current.reconnect());
  await waitFor(() => expect(result.current.run?.tool_states?.t.text).toBe('RESTORED_TAIL'));
  expect(result.current.stopping).toBe(true);
  expect(result.current.run?.tool_states?.t.state).toBe('running');
});

it('ignores a late event callback from an aborted watch in the same session', async () => {
  const { api, result } = setup();
  let oldFrame!: Parameters<WebApi['events']>[3];
  vi.mocked(api.events).mockImplementation(async (id, _after, signal, frame) => {
    if (id === 'r') oldFrame = frame;
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve()));
  });
  vi.spyOn(api, 'session').mockResolvedValue({ ...session('s'), live });
  await act(async () => result.current.selectSession('s'));
  vi.mocked(api.session).mockResolvedValue({ ...session('s'), live: { ...live, run_id: 'new' } });
  await act(async () => result.current.selectSession('s'));
  await waitFor(() => expect(result.current.run?.run_id).toBe('new'));
  act(() => oldFrame({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'r',
    sequence: 1, event_type: 'control_accepted', data: { kind: 'stop' } } }));
  expect(result.current.run?.run_id).toBe('new');
  expect(result.current.stopping).toBe(false);
});
