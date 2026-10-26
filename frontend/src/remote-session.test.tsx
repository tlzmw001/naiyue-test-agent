import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import { useConversation } from './useConversation';
import type { SessionView } from './types';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
function fixture() {
  vi.useFakeTimers();
  const api = new WebApi('test-access');
  const row: SessionView = { session_id: 's', model_id: 'A', workspace_root: null, state: 'ready',
    updated_at: '', last_run_status: null, conversation_revision: 0, messages: [], busy: false, can_run: true,
    has_compaction: false, active_tools: [], permission_mode: 'Default', manual_review_required: false,
    active_run_id: null, latest_run_id: null, pending_approval: false, live: null };
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([row]);
  vi.spyOn(api, 'prepare').mockResolvedValue(row);
  vi.spyOn(api, 'session').mockResolvedValue(row);
  vi.spyOn(api, 'start'); vi.spyOn(api, 'recover');
  const events = vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }));
  });
  return { api, row, events };
}
it('discovers a run started elsewhere after opening an idle session', async () => {
  const { api, row, events } = fixture();
  const view = renderHook(() => useConversation(api));
  await act(async () => {});
  await act(async () => view.result.current.selectSession('s'));
  vi.mocked(api.session).mockResolvedValue({ ...row, busy: true, can_run: false,
    live: { session_id: 's', run_id: 'remote', status: 'running', messages: [], last_sequence: 0, incomplete: false, manual_review_required: false, done: false } });
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(view.result.current.run?.run_id).toBe('remote');
  expect(events).toHaveBeenCalledTimes(1);
  expect(api.prepare).toHaveBeenCalledTimes(1);
  expect(api.start).not.toHaveBeenCalled(); expect(api.recover).not.toHaveBeenCalled();
  view.unmount();
  expect(events.mock.calls[0][2].aborted).toBe(true);
});
it('also finds a short completed remote run and preserves the local draft', async () => {
  const { api, row } = fixture();
  const view = renderHook(() => useConversation(api));
  await act(async () => {});
  await act(async () => view.result.current.selectSession('s'));
  act(() => view.result.current.setDraft('unsent'));
  vi.mocked(api.session).mockResolvedValue({ ...row, conversation_revision: 1,
    messages: [{ id: 'remote', type: 'ai', content: 'remote answer', state: 'complete', provisional: false }],
    live: { session_id: 's', run_id: 'short', status: 'completed', messages: [], last_sequence: 2, incomplete: false, manual_review_required: false, done: true } });
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  expect(view.result.current.messages[0].content).toBe('remote answer');
  expect(view.result.current.draft).toBe('unsent');
  expect(api.start).not.toHaveBeenCalled();
});
it('aborts an idle read when navigating and ignores its late result', async () => {
  const { api, row } = fixture();
  let finish!: (value: SessionView) => void;
  vi.mocked(api.session).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = renderHook(() => useConversation(api));
  await act(async () => {});
  await act(async () => view.result.current.selectSession('s'));
  await act(async () => vi.advanceTimersByTimeAsync(3000));
  vi.mocked(api.prepare).mockResolvedValue({ ...row, session_id: 'other' });
  await act(async () => view.result.current.selectSession('other'));
  expect(vi.mocked(api.session).mock.calls[0][1]?.aborted).toBe(true);
  await act(async () => finish({ ...row, conversation_revision: 9 }));
  expect(view.result.current.session?.session_id).toBe('other');
});
