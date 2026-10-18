import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, errorText, WebApi } from './api';
import { useConversation } from './useConversation';
import type { RunView, SessionView } from './types';
import type { SseFrame } from './sse';

afterEach(cleanup);

function setup() {
  const api = new WebApi('test-access');
  const views: Record<string, SessionView> = Object.fromEntries(['A', 'B'].map(id => [id, {
    session_id: id, conversation_revision: 0, title: id, model_id: 'model', workspace_root: '/workspace', state: 'ready',
    updated_at: '', last_run_status: null, messages: [], busy: false, can_run: true, has_compaction: false,
    active_run_id: null, latest_run_id: null, active_tools: [], permission_mode: 'Default',
    manual_review_required: false, pending_approval: false, live: null,
  }]));
  const runs: Record<string, RunView> = {};
  const streams: Array<{ rid: string; signal: AbortSignal; receive: (frame: SseFrame) => void }> = [];
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'model', model_ids: ['model'] });
  vi.spyOn(api, 'sessions').mockImplementation(async () => Object.values(views));
  vi.spyOn(api, 'session').mockImplementation(async sid => views[sid]);
  vi.spyOn(api, 'prepare').mockImplementation(async sid => views[sid]);
  vi.spyOn(api, 'run').mockImplementation(async rid => runs[rid]);
  vi.spyOn(api, 'start').mockImplementation(async sid => {
    const rid = `run-${sid}`;
    const live: RunView = { session_id: sid, run_id: rid, status: 'running', messages: [],
      last_sequence: 0, incomplete: false, done: false, manual_review_required: false };
    runs[rid] = live;
    views[sid] = { ...views[sid], state: 'running', busy: true, can_run: false,
      active_run_id: rid, latest_run_id: rid, live };
    return { session_id: sid, run_id: rid };
  });
  vi.spyOn(api, 'control').mockResolvedValue({ accepted: true, retained: [] });
  vi.spyOn(api, 'events').mockImplementation((rid, _after, signal, receive) => {
    streams.push({ rid, signal, receive });
    return new Promise(resolve => signal.addEventListener('abort', () => resolve()));
  });
  return { api, views, runs, streams, ...renderHook(() => useConversation(api)) };
}

it('switches between two running sessions with independent drafts and stop targets', async () => {
  const { api, streams, result } = setup();
  await waitFor(() => expect(result.current.model).toBe('model'));
  await act(async () => result.current.selectSession('A'));
  act(() => result.current.setDraft('task A'));
  await act(async () => result.current.submit(false));
  await waitFor(() => expect(streams).toHaveLength(1));
  act(() => result.current.setDraft('A unsent'));
  await act(async () => result.current.selectSession('B'));
  expect(streams[0].signal.aborted).toBe(true);
  expect(api.control).not.toHaveBeenCalled();
  act(() => result.current.setDraft('task B'));
  await act(async () => result.current.submit(false));
  await waitFor(() => expect(streams).toHaveLength(2));
  act(() => result.current.setDraft('B unsent'));
  expect(vi.mocked(api.start).mock.calls.map(call => call.slice(0, 2))).toEqual([['A', 'task A'], ['B', 'task B']]);
  act(() => streams[0].receive({ event: 'runtime', data: { version: 1, session_id: 'A', run_id: 'run-A',
    sequence: 1, event_type: 'run_finished', source: null,
    data: { status: 'completed', stop_mode: null, manual_review_required: false, failure: null } } }));
  expect(result.current.run?.run_id).toBe('run-B');
  expect(result.current.run?.status).toBe('running');
  expect(result.current.draft).toBe('B unsent');
  await act(async () => result.current.selectSession('A'));
  await waitFor(() => expect(streams).toHaveLength(3));
  expect(streams[1].signal.aborted).toBe(true);
  expect(result.current.draft).toBe('A unsent');
  await act(async () => result.current.stop());
  expect(api.control).toHaveBeenCalledExactlyOnceWith('run-A', 'stop');
  await act(async () => result.current.selectSession('B'));
  expect(result.current.run?.run_id).toBe('run-B');
  expect(result.current.run?.status).toBe('running');
  expect(result.current.draft).toBe('B unsent');
});

it('keeps a capacity-rejected draft and distinguishes it from the current session being busy', async () => {
  const { api, result } = setup();
  await waitFor(() => expect(result.current.model).toBe('model'));
  await act(async () => result.current.selectSession('B'));
  act(() => result.current.setDraft('keep task B'));
  vi.mocked(api.start).mockRejectedValueOnce(new ApiError('run_capacity_exceeded'));
  await act(async () => result.current.submit(false));
  expect(result.current.draft).toBe('keep task B');
  expect(result.current.notice).toContain('运行名额已满');
  expect(api.start).toHaveBeenCalledExactlyOnceWith('B', 'keep task B', undefined, 0);
  expect(api.run).not.toHaveBeenCalled();
  expect(errorText(new ApiError('session_busy'))).toContain('当前会话');
  expect(errorText(new ApiError('session_busy'))).not.toContain('另一个会话');
  await act(async () => result.current.submit(false));
  expect(api.start).toHaveBeenCalledTimes(2);
  expect(result.current.run?.run_id).toBe('run-B');
});

it('keeps the paused approval and draft after capacity refusal without resending its decision', async () => {
  const { api, views, result } = setup();
  const batch = { approval_batch_id: 'batch-B', kind: 'paused_root' as const, agent_path: 'root',
    agent: null, origin_run_id: 'origin-B', items: [
      { approval_id: 'row', tool_call_id: 'call', tool_name: 'read', target: '/outside' },
    ] };
  views.B = { ...views.B, state: 'interrupted', can_run: false, pending_approval: true, approval_batches: [batch] };
  vi.spyOn(api, 'approve').mockRejectedValue(new ApiError('run_capacity_exceeded'));
  await waitFor(() => expect(result.current.model).toBe('model'));
  await act(async () => result.current.selectSession('B'));
  act(() => result.current.setDraft('next B task'));
  await act(async () => result.current.approve(batch, [{ approval_id: 'row', decision: 'approve' }], ['row']));
  expect(result.current.session?.approval_batches).toEqual([batch]);
  expect(result.current.draft).toBe('next B task');
  expect(result.current.notice).toContain('运行名额已满');
  expect(api.approve).toHaveBeenCalledExactlyOnceWith('B', batch, [{ approval_id: 'row', decision: 'approve' }], ['row']);
  expect(api.start).not.toHaveBeenCalled();
});
