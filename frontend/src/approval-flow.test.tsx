import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import { useConversation } from './useConversation';
import type { RunView, SessionView } from './types';
import type { SseFrame } from './sse';

afterEach(cleanup);
const paused: RunView = { session_id: 's', run_id: 'origin', status: 'interrupted', messages: [],
  last_sequence: 9, incomplete: false, done: true, manual_review_required: false };
const batch = { approval_batch_id: 'batch', kind: 'paused_root' as const, agent_path: 'root', agent: null, origin_run_id: 'origin',
  items: [{ approval_id: 'row', tool_call_id: 't', tool_name: 'read', target: '/outside' }] };
const pending: SessionView = { session_id: 's', model_id: 'A', workspace_root: '/workspace', state: 'interrupted',
  updated_at: '', last_run_status: 'interrupted', messages: [], busy: false, can_run: false, has_compaction: false,
  active_run_id: null, latest_run_id: 'origin', active_tools: [], permission_mode: 'Default', manual_review_required: false, pending_approval: true,
  approval_batches: [batch], live: paused };
function setup() {
  const api = new WebApi('synthetic-test-access');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  vi.spyOn(api, 'session').mockResolvedValue(pending);
  const hook = renderHook(() => useConversation(api));
  return { api, ...hook };
}
it('loads pending approvals even if the run finished before the stream subscription', async () => {
  const { api, result } = setup();
  vi.spyOn(api, 'create').mockResolvedValue({ session_id: 's' });
  vi.spyOn(api, 'start').mockResolvedValue({ session_id: 's', run_id: 'origin' });
  vi.spyOn(api, 'run').mockResolvedValue(paused);
  vi.mocked(api.session).mockResolvedValueOnce({ ...pending, pending_approval: false, approval_batches: [] });
  await waitFor(() => expect(result.current.model).toBe('A'));
  act(() => result.current.setDraft('first'));
  await act(async () => result.current.submit(false));
  await waitFor(() => expect(result.current.session?.pending_approval).toBe(true));
  expect(result.current.session?.approval_batches).toHaveLength(1);
});
it('submits the original batch and refreshes the new run while preserving draft text', async () => {
  const { api, result } = setup();
  await act(async () => result.current.selectSession('s'));
  act(() => result.current.setDraft('later question'));
  vi.spyOn(api, 'approve').mockResolvedValue({ session_id: 's', run_id: 'resumed' });
  const completed = { ...paused, run_id: 'resumed', status: 'completed' as const };
  vi.spyOn(api, 'run').mockResolvedValue(completed);
  vi.mocked(api.session).mockResolvedValue({ ...pending, pending_approval: false, approval_batches: [], can_run: true, live: completed });
  await act(async () => result.current.approve(batch, [{ approval_id: 'row', decision: 'reject' }]));
  await waitFor(() => expect(result.current.session?.pending_approval).toBe(false));
  expect(api.approve).toHaveBeenCalledWith('s', batch, [{ approval_id: 'row', decision: 'reject' }], []);
  expect(result.current.run?.run_id).toBe('resumed');
  expect(result.current.draft).toBe('later question');
});

it('refreshes a live child batch and submits without replacing its active SSE subscription', async () => {
  const { api, result } = setup();
  const liveBatch = { ...batch, kind: 'live_child' as const, agent: 'tester', agent_path: 'root/tester/A' };
  const live = { ...paused, status: 'running' as const, done: false };
  const active = { ...pending, state: 'running', busy: true, live, approval_batches: [] };
  vi.mocked(api.session).mockResolvedValue(active);
  const run = vi.spyOn(api, 'run');
  let receive!: (frame: SseFrame) => void;
  const events = vi.spyOn(api, 'events').mockImplementation((_run, _after, signal, callback) => {
    receive = callback;
    return new Promise(resolve => signal.addEventListener('abort', () => resolve()));
  });
  await act(async () => result.current.selectSession('s'));
  await waitFor(() => expect(events).toHaveBeenCalledTimes(1));
  vi.mocked(api.session).mockResolvedValue({ ...active, pending_approval: true, approval_batches: [liveBatch] });
  act(() => receive({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'origin', sequence: 10,
    event_type: 'approval_requested', data: {}, source: { agent_path: 'root/tester/A', agent_id: 'A',
      agent_run_id: 'segment', parent_run_id: 'origin', parent_tool_call_id: 'outer' } } }));
  await waitFor(() => expect(result.current.session?.approval_batches).toHaveLength(1));
  vi.mocked(api.session).mockResolvedValue({ ...active, pending_approval: false });
  vi.spyOn(api, 'approve').mockResolvedValue({ session_id: 's', run_id: 'origin', accepted: true, kind: 'live_child' });
  await act(async () => result.current.approve(liveBatch, [{ approval_id: 'row', decision: 'approve' }], ['row']));
  expect(result.current.session?.approval_batches).toEqual([]);
  expect(result.current.run?.last_sequence).toBe(10);
  expect(result.current.run?.status).toBe('running');
  expect(events).toHaveBeenCalledTimes(1);
  expect(run).not.toHaveBeenCalled();
  expect(events.mock.calls[0][2].aborted).toBe(false);
});

it('stops a live child without stopping its parent or reconnecting SSE', async () => {
  const { api, result } = setup();
  const liveBatch = { ...batch, kind: 'live_child' as const, agent_path: 'root/tester/A' };
  const live = { ...paused, status: 'running' as const, done: false };
  const active = { ...pending, busy: true, live, approval_batches: [liveBatch] };
  vi.mocked(api.session).mockResolvedValue(active);
  const events = vi.spyOn(api, 'events').mockImplementation((_run, _after, signal) =>
    new Promise(resolve => signal.addEventListener('abort', () => resolve())));
  const stop = vi.spyOn(api, 'control').mockResolvedValue({ accepted: true, retained: [] });
  await act(async () => result.current.selectSession('s'));
  await waitFor(() => expect(events).toHaveBeenCalledTimes(1));
  vi.mocked(api.session).mockResolvedValue({ ...active, pending_approval: false, approval_batches: [] });
  await act(async () => result.current.stopApproval(liveBatch));
  expect(stop).toHaveBeenCalledWith('origin', 'stop', undefined, 'root/tester/A');
  expect(result.current.stopping).toBe(false);
  expect(result.current.pendingMode).toBe(false);
  expect(result.current.run?.status).toBe('running');
  expect(events).toHaveBeenCalledTimes(1);
  expect(events.mock.calls[0][2].aborted).toBe(false);
});

it('stops a paused root through a new run while retaining the pending draft', async () => {
  const { api, result } = setup();
  await act(async () => result.current.selectSession('s'));
  act(() => result.current.setDraft('next task'));
  const stop = vi.spyOn(api, 'stopApproval').mockResolvedValue({ session_id: 's', run_id: 'stopped-run' });
  vi.spyOn(api, 'run').mockResolvedValue({ ...paused, run_id: 'stopped-run', status: 'stopped' });
  vi.mocked(api.session).mockResolvedValue({ ...pending, pending_approval: false, approval_batches: [], can_run: true });
  await act(async () => result.current.stopApproval(batch));
  await waitFor(() => expect(result.current.session?.pending_approval).toBe(false));
  expect(stop).toHaveBeenCalledWith('s', batch);
  expect(result.current.run?.status).toBe('stopped');
  expect(result.current.draft).toBe('next task');
  expect(result.current.pendingMode).toBe(true);
});

it.each(['approve', 'stopApproval'] as const)('removes an acknowledged root batch before %s loads the resumed run', async action => {
  const { api, result } = setup();
  await act(async () => result.current.selectSession('s'));
  act(() => result.current.setDraft('keep this draft'));
  let acknowledge!: (value: { session_id: string; run_id: string }) => void;
  const post = vi.spyOn(api, action).mockImplementation(() =>
    new Promise<{ session_id: string; run_id: string }>(resolve => { acknowledge = resolve; }));
  let loadRun!: (value: RunView) => void;
  vi.spyOn(api, 'run').mockImplementation(() => new Promise(resolve => { loadRun = resolve; }));
  vi.spyOn(api, 'events').mockImplementation((_run, _after, signal) =>
    new Promise(resolve => signal.addEventListener('abort', () => resolve())));
  let submission!: Promise<void>;
  act(() => { submission = action === 'approve'
    ? result.current.approve(batch, [{ approval_id: 'row', decision: 'approve' }])
    : result.current.stopApproval(batch); });
  expect(result.current.session?.approval_batches).toEqual([batch]);
  await act(async () => acknowledge({ session_id: 's', run_id: 'resumed' }));
  expect(result.current.session?.approval_batches).toEqual([]);
  expect(result.current.session?.pending_approval).toBe(false);
  expect(result.current.submitting).toBe(true);
  expect(result.current.draft).toBe('keep this draft');
  await act(async () => {
    loadRun({ ...paused, run_id: 'resumed', status: 'running', done: false });
    await submission;
  });
  expect(result.current.run?.status).toBe('running');
  expect(result.current.session?.approval_batches).toEqual([]);
  expect(post).toHaveBeenCalledTimes(1);
});

it('removes only the accepted live child batch while session refresh is pending', async () => {
  const { api, result } = setup();
  const liveBatch = { ...batch, kind: 'live_child' as const, agent_path: 'root/tester/A' };
  const sibling = { ...liveBatch, approval_batch_id: 'sibling', agent_path: 'root/tester/B' };
  const live = { ...paused, status: 'running' as const, done: false };
  const active = { ...pending, busy: true, live, approval_batches: [liveBatch, sibling] };
  vi.mocked(api.session).mockResolvedValue(active);
  const events = vi.spyOn(api, 'events').mockImplementation((_run, _after, signal) =>
    new Promise(resolve => signal.addEventListener('abort', () => resolve())));
  await act(async () => result.current.selectSession('s'));
  let refresh!: (value: SessionView) => void;
  vi.mocked(api.session).mockImplementationOnce(() => new Promise(resolve => { refresh = resolve; }));
  const post = vi.spyOn(api, 'approve').mockResolvedValue({ session_id: 's', run_id: 'origin', accepted: true, kind: 'live_child' });
  let submission!: Promise<void>;
  await act(async () => { submission = result.current.approve(liveBatch, [{ approval_id: 'row', decision: 'approve' }]); });
  expect(result.current.session?.approval_batches).toEqual([sibling]);
  expect(result.current.session?.pending_approval).toBe(true);
  await act(async () => { refresh({ ...active, approval_batches: [sibling] }); await submission; });
  expect(result.current.run?.status).toBe('running');
  expect(events).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledTimes(1);
});

it('does not restore an accepted batch from a refresh started before its receipt', async () => {
  const { api, result } = setup();
  const liveBatch = { ...batch, kind: 'live_child' as const, agent_path: 'root/tester/A' };
  const live = { ...paused, status: 'running' as const, done: false };
  const active = { ...pending, busy: true, live, approval_batches: [liveBatch] };
  vi.mocked(api.session).mockResolvedValue(active);
  let receive!: (frame: SseFrame) => void;
  vi.spyOn(api, 'events').mockImplementation((_run, _after, signal, callback) => {
    receive = callback;
    return new Promise(resolve => signal.addEventListener('abort', () => resolve()));
  });
  await act(async () => result.current.selectSession('s'));
  let staleRefresh!: (value: SessionView) => void;
  const replacement = { ...liveBatch, approval_batch_id: 'replacement',
    items: [{ ...batch.items[0], approval_id: 'next-row', tool_call_id: 'next-call' }] };
  const current = { ...active, approval_batches: [replacement] };
  vi.mocked(api.session).mockImplementationOnce(() => new Promise(resolve => { staleRefresh = resolve; })).mockResolvedValue(current);
  act(() => receive({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'origin', sequence: 10,
    event_type: 'approval_requested', data: {} } }));
  const post = vi.spyOn(api, 'approve').mockResolvedValue({ session_id: 's', run_id: 'origin', accepted: true, kind: 'live_child' });
  await act(async () => result.current.approve(liveBatch, [{ approval_id: 'row', decision: 'approve' }]));
  expect(result.current.session?.approval_batches).toEqual([replacement]);
  await act(async () => staleRefresh(active));
  expect(result.current.session?.approval_batches).toEqual([replacement]);
  expect(result.current.session?.pending_approval).toBe(true);
  expect(api.session).toHaveBeenCalledTimes(4);
  expect(post).toHaveBeenCalledTimes(1);
});

it('keeps a rejected submission visible and available for explicit retry', async () => {
  const { api, result } = setup();
  await act(async () => result.current.selectSession('s'));
  const post = vi.spyOn(api, 'approve').mockRejectedValue(new ApiError('run_capacity_exceeded'));
  const run = vi.spyOn(api, 'run');
  await act(async () => result.current.approve(batch, [{ approval_id: 'row', decision: 'approve' }]));
  expect(result.current.session?.approval_batches).toEqual([batch]);
  expect(result.current.session?.pending_approval).toBe(true);
  expect(result.current.submitting).toBe(false);
  expect(result.current.notice).toContain('名额已满');
  expect(run).not.toHaveBeenCalled();
  expect(post).toHaveBeenCalledTimes(1);
});

it('keeps an accepted approval removed when loading the resumed run fails', async () => {
  const { api, result } = setup();
  await act(async () => result.current.selectSession('s'));
  vi.spyOn(api, 'approve').mockResolvedValue({ session_id: 's', run_id: 'resumed' });
  vi.spyOn(api, 'run').mockRejectedValue(new Error('snapshot unavailable'));
  vi.mocked(api.session).mockRejectedValue(new Error('refresh unavailable'));
  await act(async () => result.current.approve(batch, [{ approval_id: 'row', decision: 'approve' }]));
  expect(result.current.session?.approval_batches).toEqual([]);
  expect(result.current.session?.pending_approval).toBe(false);
  expect(result.current.notice).toContain('刷新未确认');
});
