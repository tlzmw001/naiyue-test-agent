import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { useSubagentList } from './useSubagentList';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import { useConversation } from './useConversation';
import { useSubagents } from './useSubagents';
import type { HistoryPage, MessageRow, RunView, SessionView } from './types';

afterEach(cleanup);
const row = (n: number): MessageRow => ({ id: `m${n}`, sequence: n, revision: 1,
  type: 'ai', content: `message ${n}`, provisional: false, state: 'complete' });
const page = (numbers: number[], cursor: string | null): HistoryPage => ({ messages: numbers.map(row),
  has_more: !!cursor, next_cursor: cursor, has_compaction: false,
  coverage: { checkpoint_id: 'cp', sync_revision: 1, history_version: 0, upper: 8 } });
function session(p = page([3, 4], 'before-3')): SessionView {
  const { messages, ...history } = p;
  return { session_id: 's', model_id: 'A', workspace_root: null, state: 'ready', updated_at: '', last_run_status: null,
    messages, history, active_tools: [], permission_mode: 'Default', busy: false, can_run: true, has_compaction: false,
    active_run_id: null, latest_run_id: null, manual_review_required: false, pending_approval: false, live: null };
}
function client() {
  const api = new WebApi('synthetic-access');
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  vi.spyOn(api, 'prepare').mockResolvedValue(session());
  vi.spyOn(api, 'session').mockResolvedValue(session());
  vi.spyOn(api, 'messages').mockResolvedValue(page([1, 2], null));
  return api;
}

it('connects root pagination and invalidates it on delete while preserving the draft', async () => {
  const api = client();
  vi.spyOn(api, 'deleteSession').mockResolvedValue({ session_id: 's' });
  const { result } = renderHook(() => useConversation(api));
  await waitFor(() => expect(result.current.model).toBe('A'));
  await act(() => result.current.selectSession('s'));
  act(() => result.current.setDraft('retained draft'));
  await act(() => result.current.history.loadOlder());
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4]);
  expect(result.current.draft).toBe('retained draft');
  await act(() => result.current.deleteSession('s'));
  expect(result.current.messages).toEqual([]);
  expect(result.current.history.enabled).toBe(false);
});

it('prepares and fills a saved gap on SSE resync without retrying execution', async () => {
  const api = client();
  const live = { session_id: 's', run_id: 'r', messages: [row(3), row(4)], status: 'running', last_sequence: 2,
    done: false, incomplete: false, manual_review_required: false } as RunView;
  vi.mocked(api.prepare).mockResolvedValueOnce({ ...session(), live }).mockResolvedValue({ ...session(page([7, 8], 'before-7')), live });
  vi.spyOn(api, 'run').mockResolvedValue({ ...live, messages: [row(7), row(8)], last_sequence: 20 });
  vi.mocked(api.messages).mockResolvedValue(page([5, 6], 'before-5'));
  vi.spyOn(api, 'events').mockRejectedValueOnce(new ApiError('resync_required')).mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve()));
  });
  vi.spyOn(api, 'start'); vi.spyOn(api, 'approve');
  const { result } = renderHook(() => useConversation(api));
  await waitFor(() => expect(result.current.model).toBe('A'));
  await act(() => result.current.selectSession('s'));
  await waitFor(() => expect(result.current.messages.map(m => m.sequence)).toEqual([3, 4, 5, 6, 7, 8]));
  expect(api.prepare).toHaveBeenCalledTimes(2);
  expect(api.start).not.toHaveBeenCalled(); expect(api.approve).not.toHaveBeenCalled();
});

it('keeps loaded child pages through detail refreshes and isolates a later selection', async () => {
  const api = client();
  const record = { agent_id: 'child', agent_path: 'root/tester/child', agent: 'tester', task: 'test',
    tool_call_id: 'outer', status: 'completed' as const };
  vi.spyOn(api, 'subagents').mockResolvedValue([record]);
  vi.spyOn(api, 'subagent').mockResolvedValue({ ...record, ...page([3, 4], 'before-3'), available: true, tool_states: {} });
  const { result } = renderHook(() => {
    const list = useSubagentList(api, 's');
    return useSubagents(api, 's', list, null, 'child');
  });
  await waitFor(() => expect(result.current.messages).toHaveLength(2));
  await act(() => result.current.history.loadOlder());
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4]);
  act(() => result.current.retry());
  await waitFor(() => expect(api.subagent).toHaveBeenCalledTimes(2));
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4]);
  act(() => result.current.select());
  expect(result.current.messages).toEqual([]);
});

it('retains current child events when the first saved detail arrives later', async () => {
  const api = client();
  const record = { agent_id: 'child', agent_path: 'root/tester/child', agent: 'tester', task: 'test',
    tool_call_id: 'outer', status: 'running' as const };
  vi.spyOn(api, 'subagents').mockResolvedValue([record]);
  let resolve!: (value: any) => void;
  vi.spyOn(api, 'subagent').mockImplementation(() => new Promise(done => { resolve = done; }));
  const live = { source: { agent_id: 'child', agent_path: record.agent_path, agent_run_id: 'cr', parent_run_id: 'r', parent_tool_call_id: 'outer' },
    status: 'running', messages: [row(5)], tool_states: {} };
  const run = { session_id: 's', run_id: 'r', agents: { [record.agent_path]: live }, messages: [] } as unknown as RunView;
  const { result } = renderHook(() => {
    const list = useSubagentList(api, 's', run);
    return useSubagents(api, 's', list, run, 'child');
  });
  await waitFor(() => expect(result.current.messages.some(m => m.id === 'm5')).toBe(true));
  await act(async () => resolve({ ...record, ...page([3, 4], 'before-3'), available: true, tool_states: {} }));
  expect(result.current.messages.map(m => m.sequence)).toEqual([3, 4, 5]);
});

it.each(['compact', 'message'] as const)('keeps loaded old pages and the draft through a %s compaction result', async mode => {
  const api = client();
  vi.spyOn(api, 'start').mockResolvedValue({ session_id: 's', run_id: 'r' });
  vi.spyOn(api, 'compact').mockResolvedValue({ session_id: 's', run_id: 'r' });
  vi.spyOn(api, 'run').mockResolvedValue({ session_id: 's', run_id: 'r', mode, status: 'completed',
    messages: [row(5), row(6)], last_sequence: 10, incomplete: false, done: true,
    manual_review_required: false, compaction_result: { status: 'applied' } });
  const { result } = renderHook(() => useConversation(api));
  await waitFor(() => expect(result.current.model).toBe('A'));
  await act(() => result.current.selectSession('s'));
  await act(() => result.current.history.loadOlder());
  const latest = session(page([5, 6], 'before-5'));
  latest.has_compaction = true;
  vi.mocked(api.session).mockResolvedValue(latest);
  act(() => result.current.setDraft(mode === 'compact' ? 'retained draft' : 'next task'));
  if (mode === 'compact') await act(() => result.current.compact());
  else await act(() => result.current.submit(false));
  await waitFor(() => expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4, 5, 6]));
  expect(result.current.session?.has_compaction).toBe(true);
  expect(result.current.draft).toBe(mode === 'compact' ? 'retained draft' : '');
  expect(api.messages).toHaveBeenCalledTimes(1);
  expect(mode === 'compact' ? api.compact : api.start).toHaveBeenCalledTimes(1);
});

it('merges automatic restart recovery into the original archive without starting execution', async () => {
  const api = client();
  const cold = session(page([3, 4], 'before-3'));
  cold.can_run = false;
  cold.checkpoint_id = 'saved-checkpoint';
  cold.recovery = { kind: 'interrupted_execution', reason: 'previous_run_unfinished', actions: ['continue'],
    unconfirmed_tool_call_ids: ['tool'] };
  vi.mocked(api.prepare).mockResolvedValue(cold);
  const ready = session(page([4, 5], 'before-4'));
  ready.messages[1] = { ...row(5), type: 'tool', tool_call_id: 'tool', parent_message_id: 'm4',
    content: 'result unconfirmed', status: 'error' };
  vi.spyOn(api, 'recover').mockResolvedValue(ready);
  vi.spyOn(api, 'start'); vi.spyOn(api, 'approve');
  const { result } = renderHook(() => useConversation(api));
  await waitFor(() => expect(result.current.model).toBe('A'));
  await act(() => result.current.selectSession('s'));
  act(() => result.current.setDraft('draft after recovery'));
  await act(() => result.current.history.loadOlder());
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4, 5]);
  expect(result.current.messages.at(-1)?.parent_message_id).toBe('m4');
  expect(result.current.draft).toBe('draft after recovery');
  expect(api.recover).toHaveBeenCalledWith('s', 'saved-checkpoint', 'continue');
  expect(api.start).not.toHaveBeenCalled(); expect(api.approve).not.toHaveBeenCalled();
});
