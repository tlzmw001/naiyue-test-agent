import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import { useConversation } from './useConversation';
import type { SessionView } from './types';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
async function setup() {
  const api = new WebApi('synthetic', '11111111-1111-1111-1111-111111111111');
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A', 'B'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  const view: SessionView = { session_id: 'S', model_id: 'A', messages: [], can_run: true, live: null,
    workspace_root: null, state: 'ready', updated_at: '2026-10-26T10:00:00Z', last_run_status: null,
    active_tools: [], permission_mode: 'Default', busy: false, has_compaction: false,
    active_run_id: null, latest_run_id: null, manual_review_required: false, pending_approval: false };
  vi.spyOn(api, 'session').mockResolvedValue(view);
  vi.spyOn(api, 'create').mockResolvedValue({ session_id: 'S' });
  vi.spyOn(api, 'start').mockResolvedValue({ session_id: 'S', run_id: 'R' });
  vi.spyOn(api, 'run').mockResolvedValue({ session_id: 'S', run_id: 'R', status: 'completed', messages: [],
    done: true, last_sequence: 0, incomplete: false, manual_review_required: false });
  vi.spyOn(api, 'events').mockResolvedValue(undefined);
  const hook = renderHook(() => useConversation(api));
  await waitFor(() => expect(hook.result.current.model).toBe('A'));
  return { api, view, ...hook };
}

it('selects a draft without creating and creates the local session on first send', async () => {
  const { api, result } = await setup();
  act(() => result.current.setDraft('test this project'));
  await act(async () => { await result.current.newSession(); });
  expect(api.create).not.toHaveBeenCalled();
  expect(result.current.draft).toBe('test this project');
  await act(async () => { await result.current.submit(false); });
  expect(api.create).toHaveBeenCalledTimes(1);
  expect(api.create).toHaveBeenCalledWith('A', expect.any(String));
  expect(api.start).toHaveBeenCalledWith('S', 'test this project', undefined, undefined);
  expect(result.current.draft).toBe('');
});

it('keeps the first request and model after an unconfirmed creation and a menu revisit', async () => {
  const { api, result } = await setup();
  vi.mocked(api.create).mockRejectedValueOnce(new Error('lost response'));
  await act(async () => { await result.current.newSession(); });
  act(() => result.current.setDraft('preserve me'));
  await act(async () => { await result.current.submit(false); });
  expect(result.current.draft).toBe('preserve me');
  expect(api.start).not.toHaveBeenCalled();
  await act(async () => { await result.current.newSession(); await result.current.selectModel('B'); });
  await act(async () => { await result.current.submit(false); });
  expect(api.create).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.create).mock.calls[1]).toEqual(vi.mocked(api.create).mock.calls[0]);
});


it('keeps draft text visible and reports preparation while the first response is pending', async () => {
  const { api, result } = await setup();
  let finish!: (value: { session_id: string }) => void;
  vi.mocked(api.create).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => { await result.current.newSession(); });
  act(() => result.current.setDraft('preparing task'));
  let pending!: Promise<void>;
  act(() => { pending = result.current.submit(false); });
  await waitFor(() => expect(result.current.creation.preparing).toBe(true));
  expect(result.current.draft).toBe('preparing task'); expect(api.start).not.toHaveBeenCalled();
  await act(async () => { finish({ session_id: 'S' }); await pending; });
  expect(result.current.creation.preparing).toBe(false); expect(api.start).toHaveBeenCalledTimes(1);
});
it('keeps tools, permission and thinking in the new session draft until the first send', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'selectTools').mockResolvedValue({ active_tools: ['read'] });
  vi.spyOn(api, 'selectPermissionMode').mockResolvedValue({ permission_mode: 'NoAsk' });
  vi.spyOn(api, 'selectThinking').mockResolvedValue({ model_id: 'A', thinking_level: 'high' });
  await act(async () => { await result.current.newSession(); });
  await act(async () => { await result.current.selectTools(['read']); await result.current.selectPermissionMode('NoAsk');
    await result.current.selectThinking('high'); });
  expect(api.create).not.toHaveBeenCalled(); expect(api.create).not.toHaveBeenCalled();
  expect(api.selectTools).not.toHaveBeenCalled(); expect(api.selectPermissionMode).not.toHaveBeenCalled();
  expect(api.selectThinking).not.toHaveBeenCalled();
  act(() => result.current.setDraft('session with preferences'));
  await act(async () => { await result.current.submit(false); });
  expect(api.create).toHaveBeenCalledWith('A', expect.any(String));
  expect(api.selectTools).toHaveBeenCalledExactlyOnceWith('S', ['read']);
  expect(api.selectPermissionMode).toHaveBeenCalledExactlyOnceWith('S', 'NoAsk');
  expect(api.selectThinking).toHaveBeenCalledExactlyOnceWith('S', 'A', 'high');
  expect(vi.mocked(api.start).mock.invocationCallOrder[0]).toBeGreaterThan(vi.mocked(api.selectThinking).mock.invocationCallOrder[0]);
});
it('keeps the created session and text when a control save fails and retries only the remaining save', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'selectTools').mockRejectedValueOnce(new Error('save unconfirmed')).mockResolvedValue({ active_tools: ['read'] });
  await act(async () => { await result.current.newSession(); await result.current.selectTools(['read']); });
  act(() => result.current.setDraft('wait for settings'));
  await act(async () => { await result.current.submit(false); });
  expect(result.current.session?.session_id).toBe('S'); expect(result.current.draft).toBe('wait for settings');
  expect(api.start).not.toHaveBeenCalled();
  await act(async () => { await result.current.submit(false); });
  expect(api.create).toHaveBeenCalledTimes(1); expect(api.selectTools).toHaveBeenCalledTimes(2);
  expect(api.start).toHaveBeenCalledTimes(1);
});
it('does not reapply an older draft setting after the user overrides it in the created session', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'selectTools').mockRejectedValueOnce(new Error('save unconfirmed')).mockResolvedValue({ active_tools: ['write'] });
  await act(async () => { await result.current.newSession(); await result.current.selectTools(['read']); });
  act(() => result.current.setDraft('settings override'));
  await act(async () => { await result.current.submit(false); });
  await act(async () => { await result.current.selectTools(['write']); });
  await act(async () => { await result.current.submit(false); });
  expect(vi.mocked(api.selectTools).mock.calls).toEqual([['S', ['read']], ['S', ['write']]]);
  expect(api.start).toHaveBeenCalledTimes(1);
});
it('retries only the session read after the create receipt already identified the session', async () => {
  const { api, view, result } = await setup();
  vi.mocked(api.session).mockRejectedValueOnce(new Error('first read failed')).mockResolvedValue(view);
  await act(async () => { await result.current.newSession(); });
  act(() => result.current.setDraft('read again'));
  await act(async () => { await result.current.submit(false); });
  expect(result.current.draft).toBe('read again'); expect(api.start).not.toHaveBeenCalled();
  await act(async () => { await result.current.submit(false); });
  expect(api.create).toHaveBeenCalledTimes(1);
  expect(api.session).toHaveBeenCalledWith('S'); expect(api.start).toHaveBeenCalledTimes(1);
});
it('confirms the latest permission after both the original save and a user override fail', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'selectPermissionMode').mockRejectedValueOnce(new Error('original save unconfirmed'))
    .mockRejectedValueOnce(new Error('override save unconfirmed')).mockRejectedValueOnce(new Error('still unconfirmed'))
    .mockResolvedValue({ permission_mode: 'Default' });
  await act(async () => { await result.current.newSession(); await result.current.selectPermissionMode('NoAsk'); });
  act(() => result.current.setDraft('must use latest permission'));
  await act(async () => { await result.current.submit(false); });
  await act(async () => { await result.current.selectPermissionMode('Default'); });
  await act(async () => { await result.current.submit(false); });
  expect(api.start).not.toHaveBeenCalled(); expect(result.current.draft).toBe('must use latest permission');
  await act(async () => { await result.current.submit(false); });
  expect(vi.mocked(api.selectPermissionMode).mock.calls).toEqual([['S', 'NoAsk'], ['S', 'Default'], ['S', 'Default'], ['S', 'Default']]);
  expect(api.start).toHaveBeenCalledTimes(1); expect(api.create).toHaveBeenCalledTimes(1);
});
