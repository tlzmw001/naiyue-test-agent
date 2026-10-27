import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import { useConversation } from './useConversation';
import type { ExecutionDraft, SessionView } from './types';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const choice: ExecutionDraft = { execution: 'sandbox', include_untracked: ['notes.txt'],
  profile_id: 'python-tests', profile_name: 'Python tests', expected_profile_revision: 'a'.repeat(64) };
async function setup() {
  const api = new WebApi('synthetic', '11111111-1111-1111-1111-111111111111');
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A', 'B'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([]);
  const view: SessionView = { session_id: 'S', model_id: 'A', messages: [], can_run: true, live: null,
    workspace_root: null, state: 'ready', updated_at: '2026-10-26T10:00:00Z', last_run_status: null,
    active_tools: [], permission_mode: 'Default', busy: false, has_compaction: false,
    active_run_id: null, latest_run_id: null, manual_review_required: false, pending_approval: false,
    execution: { kind: 'sandbox', state: 'ready', profile_name: 'Python tests' } };
  vi.spyOn(api, 'session').mockResolvedValue(view);
  vi.spyOn(api, 'createSelected').mockResolvedValue({ session_id: 'S' });
  vi.spyOn(api, 'start').mockResolvedValue({ session_id: 'S', run_id: 'R' });
  vi.spyOn(api, 'run').mockResolvedValue({ session_id: 'S', run_id: 'R', status: 'completed', messages: [],
    done: true, last_sequence: 0, incomplete: false, manual_review_required: false });
  vi.spyOn(api, 'events').mockResolvedValue(undefined);
  const hook = renderHook(() => useConversation(api));
  await waitFor(() => expect(hook.result.current.model).toBe('A'));
  return { api, view, ...hook };
}

it('selects a draft without creating and creates the selected environment on first send', async () => {
  const { api, result } = await setup();
  act(() => result.current.setDraft('test this project'));
  await act(async () => { await result.current.newSession(choice); });
  expect(api.createSelected).not.toHaveBeenCalled();
  expect(result.current.draft).toBe('test this project');
  await act(async () => { await result.current.submit(false); });
  expect(api.createSelected).toHaveBeenCalledTimes(1);
  expect(api.createSelected).toHaveBeenCalledWith('A', expect.objectContaining({ execution: 'sandbox', model_id: 'A',
    profile_id: choice.profile_id, expected_profile_revision: choice.expected_profile_revision,
    include_untracked: ['notes.txt'], requestId: expect.any(String) }));
  expect(api.start).toHaveBeenCalledWith('S', 'test this project', undefined, undefined);
  expect(result.current.draft).toBe('');
});

it('keeps the first request and model after an unconfirmed creation and a menu revisit', async () => {
  const { api, result } = await setup();
  vi.mocked(api.createSelected).mockRejectedValueOnce(new Error('lost response'));
  await act(async () => { await result.current.newSession(choice); });
  act(() => result.current.setDraft('preserve me'));
  await act(async () => { await result.current.submit(false); });
  expect(result.current.draft).toBe('preserve me');
  expect(api.start).not.toHaveBeenCalled();
  await act(async () => { await result.current.newSession(); await result.current.selectModel('B'); });
  await act(async () => { await result.current.submit(false); });
  expect(api.createSelected).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.createSelected).mock.calls[1]).toEqual(vi.mocked(api.createSelected).mock.calls[0]);
});

it('preserves text and the saved session when preparation is unavailable and does not start a run', async () => {
  const { api, view, result } = await setup();
  vi.mocked(api.session).mockResolvedValue({ ...view, can_run: false, execution: { ...view.execution!, state: 'unavailable' } });
  await act(async () => { await result.current.newSession(choice); });
  act(() => result.current.setDraft('not sent yet'));
  await act(async () => { await result.current.submit(false); });
  expect(result.current.session?.session_id).toBe('S');
  expect(result.current.draft).toBe('not sent yet');
  expect(api.start).not.toHaveBeenCalled();
  expect(result.current.notice).toContain('准备');
});

it('requires another explicit selection after a stale fingerprint and then creates a fresh request', async () => {
  const { api, result } = await setup();
  vi.mocked(api.createSelected).mockRejectedValueOnce(new ApiError('env_profile_changed'));
  await act(async () => { await result.current.newSession(choice); });
  act(() => result.current.setDraft('keep this'));
  await act(async () => { await result.current.submit(false); await result.current.submit(false); });
  expect(api.createSelected).toHaveBeenCalledTimes(1);
  expect(result.current.draft).toBe('keep this');
  await act(async () => { await result.current.newSession({ ...choice, expected_profile_revision: 'b'.repeat(64) }); });
  await act(async () => { await result.current.submit(false); });
  expect(api.createSelected).toHaveBeenCalledTimes(2);
  const calls = vi.mocked(api.createSelected).mock.calls;
  expect(calls[1][1]!.requestId).not.toBe(calls[0][1]!.requestId);
  expect(calls[1][1]!.expected_profile_revision).toBe('b'.repeat(64));
});
it('records explicit trust as draft consent and performs it only on first send', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'trustGit').mockResolvedValue({} as never);
  await act(async () => { await result.current.newSession({ ...choice, trust_revision: 7 }); });
  expect(api.trustGit).not.toHaveBeenCalled(); expect(api.createSelected).not.toHaveBeenCalled();
  act(() => result.current.setDraft('trusted task'));
  await act(async () => { await result.current.submit(false); });
  expect(api.trustGit).toHaveBeenCalledWith(7, expect.any(String));
  expect(vi.mocked(api.trustGit).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.createSelected).mock.invocationCallOrder[0]);
});
it('unlocks a stale trust confirmation before any session creation so it can be selected again', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'trustGit').mockRejectedValue(new ApiError('project_location_changed'));
  await act(async () => { await result.current.newSession({ ...choice, trust_revision: 7 }); });
  act(() => result.current.setDraft('not created'));
  await act(async () => { await result.current.submit(false); });
  expect(api.createSelected).not.toHaveBeenCalled();
  expect(result.current.creation.locked).toBe(false);
  expect(result.current.draft).toBe('not created');
});
it('keeps draft text visible and reports preparation while the first response is pending', async () => {
  const { api, result } = await setup();
  let finish!: (value: { session_id: string }) => void;
  vi.mocked(api.createSelected).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => { await result.current.newSession(choice); });
  act(() => result.current.setDraft('preparing task'));
  let pending!: Promise<void>;
  act(() => { pending = result.current.submit(false); });
  await waitFor(() => expect(result.current.creation.preparing).toBe(true));
  expect(result.current.draft).toBe('preparing task'); expect(api.start).not.toHaveBeenCalled();
  await act(async () => { finish({ session_id: 'S' }); await pending; });
  expect(result.current.creation.preparing).toBe(false); expect(api.start).toHaveBeenCalledTimes(1);
});
it('keeps tools, permission and thinking in the sandbox draft until the first send', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'create');
  vi.spyOn(api, 'selectTools').mockResolvedValue({ active_tools: ['read'] });
  vi.spyOn(api, 'selectPermissionMode').mockResolvedValue({ permission_mode: 'NoAsk' });
  vi.spyOn(api, 'selectThinking').mockResolvedValue({ model_id: 'A', thinking_level: 'high' });
  await act(async () => { await result.current.newSession(choice); });
  await act(async () => { await result.current.selectTools(['read']); await result.current.selectPermissionMode('NoAsk');
    await result.current.selectThinking('high'); });
  expect(api.create).not.toHaveBeenCalled(); expect(api.createSelected).not.toHaveBeenCalled();
  expect(api.selectTools).not.toHaveBeenCalled(); expect(api.selectPermissionMode).not.toHaveBeenCalled();
  expect(api.selectThinking).not.toHaveBeenCalled();
  act(() => result.current.setDraft('sandbox with preferences'));
  await act(async () => { await result.current.submit(false); });
  expect(api.createSelected).toHaveBeenCalledWith('A', expect.objectContaining({ profile_id: 'python-tests' }));
  expect(api.selectTools).toHaveBeenCalledExactlyOnceWith('S', ['read']);
  expect(api.selectPermissionMode).toHaveBeenCalledExactlyOnceWith('S', 'NoAsk');
  expect(api.selectThinking).toHaveBeenCalledExactlyOnceWith('S', 'A', 'high');
  expect(vi.mocked(api.start).mock.invocationCallOrder[0]).toBeGreaterThan(vi.mocked(api.selectThinking).mock.invocationCallOrder[0]);
});
it('keeps the created environment and text when a control save fails and retries only the remaining save', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'selectTools').mockRejectedValueOnce(new Error('save unconfirmed')).mockResolvedValue({ active_tools: ['read'] });
  await act(async () => { await result.current.newSession(choice); await result.current.selectTools(['read']); });
  act(() => result.current.setDraft('wait for settings'));
  await act(async () => { await result.current.submit(false); });
  expect(result.current.session?.session_id).toBe('S'); expect(result.current.draft).toBe('wait for settings');
  expect(api.start).not.toHaveBeenCalled();
  await act(async () => { await result.current.submit(false); });
  expect(api.createSelected).toHaveBeenCalledTimes(1); expect(api.selectTools).toHaveBeenCalledTimes(2);
  expect(api.start).toHaveBeenCalledTimes(1);
});
it('does not reapply an older draft setting after the user overrides it in the created session', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'selectTools').mockRejectedValueOnce(new Error('save unconfirmed')).mockResolvedValue({ active_tools: ['write'] });
  await act(async () => { await result.current.newSession(choice); await result.current.selectTools(['read']); });
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
  await act(async () => { await result.current.newSession(choice); });
  act(() => result.current.setDraft('read again'));
  await act(async () => { await result.current.submit(false); });
  expect(result.current.draft).toBe('read again'); expect(api.start).not.toHaveBeenCalled();
  await act(async () => { await result.current.submit(false); });
  expect(api.createSelected).toHaveBeenCalledTimes(1);
  expect(api.session).toHaveBeenCalledWith('S'); expect(api.start).toHaveBeenCalledTimes(1);
});
it('confirms the latest permission after both the original save and a user override fail', async () => {
  const { api, result } = await setup();
  vi.spyOn(api, 'selectPermissionMode').mockRejectedValueOnce(new Error('original save unconfirmed'))
    .mockRejectedValueOnce(new Error('override save unconfirmed')).mockRejectedValueOnce(new Error('still unconfirmed'))
    .mockResolvedValue({ permission_mode: 'Default' });
  await act(async () => { await result.current.newSession(choice); await result.current.selectPermissionMode('NoAsk'); });
  act(() => result.current.setDraft('must use latest permission'));
  await act(async () => { await result.current.submit(false); });
  await act(async () => { await result.current.selectPermissionMode('Default'); });
  await act(async () => { await result.current.submit(false); });
  expect(api.start).not.toHaveBeenCalled(); expect(result.current.draft).toBe('must use latest permission');
  await act(async () => { await result.current.submit(false); });
  expect(vi.mocked(api.selectPermissionMode).mock.calls).toEqual([['S', 'NoAsk'], ['S', 'Default'], ['S', 'Default'], ['S', 'Default']]);
  expect(api.start).toHaveBeenCalledTimes(1); expect(api.createSelected).toHaveBeenCalledTimes(1);
});
