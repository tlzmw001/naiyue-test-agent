import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import { useConversation } from './useConversation';
import type { RunView, SessionView } from './types';

afterEach(cleanup);
const paused: RunView = { session_id: 's', run_id: 'origin', status: 'interrupted', messages: [],
  last_sequence: 9, incomplete: false, done: true, manual_review_required: false };
const pending: SessionView = { session_id: 's', model_id: 'A', workspace_root: '/workspace', state: 'interrupted',
  updated_at: '', last_run_status: 'interrupted', messages: [], busy: false, can_run: false, has_compaction: false,
  active_run_id: null, latest_run_id: 'origin', active_tools: [], permission_mode: 'Default', manual_review_required: false, pending_approval: true,
  approval_run_id: 'origin', approval_requests: [{ tool_call_id: 't', tool_name: 'read', target: '/outside' }], live: paused };
function setup() {
  const api = new WebApi('synthetic-test-access');
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
  vi.mocked(api.session).mockResolvedValueOnce({ ...pending, pending_approval: false, approval_requests: [] });
  await waitFor(() => expect(result.current.model).toBe('A'));
  act(() => result.current.setDraft('first'));
  await act(async () => result.current.submit(false));
  await waitFor(() => expect(result.current.session?.pending_approval).toBe(true));
  expect(result.current.session?.approval_requests).toHaveLength(1);
});
it('submits the original batch and refreshes the new run while preserving draft text', async () => {
  const { api, result } = setup();
  await act(async () => result.current.selectSession('s'));
  act(() => result.current.setDraft('later question'));
  vi.spyOn(api, 'approve').mockResolvedValue({ session_id: 's', run_id: 'resumed' });
  const completed = { ...paused, run_id: 'resumed', status: 'completed' as const };
  vi.spyOn(api, 'run').mockResolvedValue(completed);
  vi.mocked(api.session).mockResolvedValue({ ...pending, pending_approval: false, approval_requests: [], can_run: true, live: completed });
  await act(async () => result.current.approve([{ tool_call_id: 't', decision: 'reject' }]));
  await waitFor(() => expect(result.current.session?.pending_approval).toBe(false));
  expect(api.approve).toHaveBeenCalledWith('s', 'origin', [{ tool_call_id: 't', decision: 'reject' }]);
  expect(result.current.run?.run_id).toBe('resumed');
  expect(result.current.draft).toBe('later question');
});
