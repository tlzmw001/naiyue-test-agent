import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import { useConversation } from './useConversation';
import type { SessionView, RunView } from './types';

afterEach(() => { cleanup(); sessionStorage.clear(); });
const project = '11111111-1111-4111-8111-111111111111';
const material = { material_id: '22222222-2222-4222-8222-222222222222', receive_id: '33333333-3333-4333-8333-333333333333',
  task_id: 'a'.repeat(32), result_id: '44444444-4444-4444-8444-444444444444', input_id: 'b'.repeat(64), title: 'report.json',
  role: 'artifact' as const, size: 12, sha256: 'c'.repeat(64), source_run_id: 'd'.repeat(32), outcome: 'completed' as const, captured_at: 1, reported: true as const };
function setup(busy = false) {
  const api = new WebApi('fixture', project);
  const run: RunView = { session_id: 's', run_id: 'run', status: busy ? 'running' : 'completed', done: !busy, messages: [], last_sequence: 0, incomplete: false, manual_review_required: false };
  const row: SessionView = { session_id: 's', model_id: 'A', workspace_root: null, state: 'ready', updated_at: '', last_run_status: null,
    messages: [], busy, can_run: !busy, has_compaction: false, active_run_id: busy ? 'run' : null, latest_run_id: null, active_tools: [],
    permission_mode: 'Default', manual_review_required: false, pending_approval: false, live: busy ? run : null };
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  vi.spyOn(api, 'session').mockResolvedValue(row); vi.spyOn(api, 'prepare').mockImplementation(async id => ({ ...row, session_id: id }));
  vi.spyOn(api, 'run').mockResolvedValue(run);
  if (busy) vi.spyOn(api, 'events').mockImplementation(async (_id, _after, signal) => {
    await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }));
  });
  return { api, start: vi.spyOn(api, 'start').mockResolvedValue({ session_id: 's', run_id: 'run' }), control: vi.spyOn(api, 'control') };
}
it.each([false, true])('preserves retired material drafts without submitting them, busy=%s', async busy => {
  sessionStorage.setItem(`naiyue.material-draft.${project}.s`, JSON.stringify({ text: '已有目标', materials: [material] }));
  const { api, start, control } = setup(busy);
  const view = renderHook(() => useConversation(api));
  await waitFor(() => expect(view.result.current.model).toBe('A'));
  await act(async () => view.result.current.selectSession('other'));
  expect(view.result.current.materialInputs.current).toEqual([]);
  await act(async () => view.result.current.selectSession('s'));
  await act(async () => view.result.current.submit(false));
  expect(start).not.toHaveBeenCalled();
  expect(control).not.toHaveBeenCalled();
  expect(view.result.current.draft).toBe('已有目标');
  expect(view.result.current.materialInputs.current).toEqual([material]);
  act(() => view.result.current.materialInputs.remove(material.material_id));
  view.unmount();
  const reopened = renderHook(() => useConversation(api));
  await waitFor(() => expect(reopened.result.current.model).toBe('A'));
  await act(async () => reopened.result.current.selectSession('s'));
  expect(reopened.result.current.draft).toBe('已有目标');
  expect(reopened.result.current.materialInputs.current).toEqual([]);
});
