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
const draft = { local_project_id: project, local_session_id: 's', receive_id: material.receive_id, text: '分析云端报告', material_refs: [material] };
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
it('merges analysis references into the existing draft once, persists them by project/session and rejects another session', async () => {
  const { api } = setup(); const view = renderHook(() => useConversation(api));
  await waitFor(() => expect(view.result.current.model).toBe('A'));
  await act(async () => view.result.current.selectSession('s'));
  act(() => view.result.current.setDraft('已有目标'));
  act(() => { view.result.current.addAnalysisDraft(draft); view.result.current.addAnalysisDraft(draft); });
  expect(view.result.current.draft).toBe('已有目标\n\n分析云端报告');
  expect(view.result.current.materialInputs.current).toEqual([material]);
  await act(async () => view.result.current.selectSession('other'));
  act(() => view.result.current.addAnalysisDraft(draft));
  expect(view.result.current.draft).toBe(''); expect(view.result.current.materialInputs.current).toEqual([]);
  view.unmount();
  const reopened = renderHook(() => useConversation(api));
  await waitFor(() => expect(reopened.result.current.model).toBe('A'));
  await act(async () => reopened.result.current.selectSession('s'));
  expect(reopened.result.current.draft).toBe('已有目标\n\n分析云端报告');
  expect(reopened.result.current.materialInputs.current).toEqual([material]);
});
it('freezes the material IDs, text and request identity through lost ACK and preserves later edits', async () => {
  const { api, start } = setup(); start.mockRejectedValueOnce(new TypeError('lost ACK'));
  const { result } = renderHook(() => useConversation(api));
  await waitFor(() => expect(result.current.model).toBe('A'));
  await act(async () => result.current.selectSession('s'));
  act(() => result.current.addAnalysisDraft(draft));
  await act(async () => result.current.submit(false));
  expect(result.current.imageInputs.uncertain?.materialRefs).toEqual([material]);
  act(() => result.current.setDraft('新编辑'));
  await act(async () => result.current.retryImageSubmission());
  expect(start).toHaveBeenCalledTimes(2);
  expect(start.mock.calls[1]).toEqual(start.mock.calls[0]);
  expect(start.mock.calls[1][6]).toEqual([material.material_id]);
  expect(result.current.draft).toBe('新编辑'); expect(result.current.materialInputs.current).toEqual([]);
});
it('keeps materials and text for the next root run instead of steering or following up a busy run', async () => {
  const { api, start, control } = setup(true); const { result } = renderHook(() => useConversation(api));
  await waitFor(() => expect(result.current.model).toBe('A'));
  await act(async () => result.current.selectSession('s'));
  act(() => result.current.addAnalysisDraft(draft));
  await act(async () => result.current.submit(true));
  expect(start).not.toHaveBeenCalled(); expect(control).not.toHaveBeenCalled();
  expect(result.current.draft).toBe(draft.text); expect(result.current.materialInputs.current).toEqual([material]);
});
