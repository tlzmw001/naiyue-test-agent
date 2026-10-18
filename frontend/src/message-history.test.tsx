import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import { useMessageHistory } from './useMessageHistory';
import { mergeMessages } from './events';
import type { HistoryPage, MessageRow } from './types';

afterEach(cleanup);
const message = (sequence: number, revision = 1): MessageRow => ({ id: `m${sequence}`, sequence, revision,
  type: 'ai', content: `message ${sequence}`, state: 'complete', provisional: false });
const page = (numbers: number[], cursor: string | null, version = 0): HistoryPage => ({
  messages: numbers.map(n => message(n)), has_more: !!cursor, next_cursor: cursor, has_compaction: false,
  coverage: { checkpoint_id: 'saved', sync_revision: 1, history_version: version, upper: 20 },
});
function setup() {
  const api = new WebApi('synthetic-access');
  const request = vi.spyOn(api, 'messages');
  const hook = renderHook(() => useMessageHistory(api));
  act(() => hook.result.current.open('s', undefined, page([3, 4], 'before-3')));
  return { ...hook, api, request };
}

it('prepends ordered pages once and stops at the beginning', async () => {
  const { result, request } = setup();
  request.mockResolvedValue(page([1, 2, 3], null));
  await act(() => result.current.loadOlder());
  expect(result.current.messages.map(m => m.id)).toEqual(['m1', 'm2', 'm3', 'm4']);
  expect(result.current.pagination.hasMore).toBe(false);
  await act(() => result.current.loadOlder());
  expect(request).toHaveBeenCalledTimes(1);
});

it('allows only one in-flight page and keeps live appends made while waiting', async () => {
  const { result, request } = setup();
  let resolve!: (value: HistoryPage) => void;
  request.mockImplementation(() => new Promise(done => { resolve = done; }));
  let pending!: Promise<void>;
  act(() => { pending = result.current.loadOlder(); });
  await act(() => result.current.loadOlder());
  act(() => result.current.merge([message(5)]));
  await act(async () => { resolve(page([1, 2], null)); await pending; });
  expect(request).toHaveBeenCalledTimes(1);
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4, 5]);
});

it('preserves data on failure and retries only when requested', async () => {
  const { result, request } = setup();
  request.mockRejectedValueOnce(new Error('fixture')).mockResolvedValueOnce(page([1, 2], null));
  await act(() => result.current.loadOlder());
  expect(result.current.messages.map(m => m.id)).toEqual(['m3', 'm4']);
  expect(result.current.pagination.error).toBeTruthy();
  expect(request).toHaveBeenCalledTimes(1);
  await act(() => result.current.loadOlder());
  expect(result.current.pagination.error).toBe('');
  expect(result.current.messages).toHaveLength(4);
});

it('discards late pages after switching the child scope or cancelling', async () => {
  const { result, request } = setup();
  let resolve!: (value: HistoryPage) => void;
  request.mockImplementation(() => new Promise(done => { resolve = done; }));
  let pending!: Promise<void>;
  act(() => { pending = result.current.loadOlder(); });
  act(() => result.current.open('s', 'other-child', page([8, 9], null)));
  await act(async () => { resolve(page([1, 2], null)); await pending; });
  expect(result.current.messages.map(m => m.id)).toEqual(['m8', 'm9']);
});

it('fills a reconnect gap without replacing already loaded older pages', async () => {
  const { result, request } = setup();
  request.mockResolvedValue(page([5, 6], 'before-5'));
  await act(() => result.current.synchronize(page([7, 8], 'before-7')));
  expect(request).toHaveBeenCalledTimes(1);
  expect(result.current.messages.map(m => m.sequence)).toEqual([3, 4, 5, 6, 7, 8]);
  expect(result.current.pagination.hasMore).toBe(true);
});

it('refreshes the loaded range after a revision change and rejects old page revisions', async () => {
  const { result, request } = setup();
  act(() => result.current.open('s', undefined, page([1, 2, 3, 4], null)));
  const revised = page([1, 2, 3, 4], null, 1);
  revised.messages[0] = { ...message(1, 2), content: 'corrected' };
  request.mockResolvedValue(revised);
  await act(() => result.current.synchronize(page([5, 6], 'before-5', 1)));
  expect(result.current.messages[0].content).toBe('corrected');
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4, 5, 6]);
  act(() => result.current.merge([message(1, 1)]));
  expect(result.current.messages[0].content).toBe('corrected');
});

it('restarts a stale cursor only on explicit retry and refreshes its loaded range', async () => {
  const { result, request } = setup();
  request.mockRejectedValueOnce(new ApiError('history_cursor_stale'))
    .mockResolvedValueOnce(page([3, 4], 'new-before-3', 1));
  await act(() => result.current.loadOlder());
  expect(result.current.pagination.error).toBeTruthy();
  expect(request).toHaveBeenCalledTimes(1);
  await act(() => result.current.loadOlder());
  expect(result.current.pagination.error).toBe('');
  expect(result.current.messages.map(m => m.sequence)).toEqual([3, 4]);
});

it('preserves full terminal bodies and archive identity across preview and live updates', () => {
  const original = { ...message(1, 2), parent_message_id: 'parent', detail: { sequence: 1, revision: 2 } };
  const truncated = { ...message(1, 1), content: 'preview', content_truncated: true };
  expect(mergeMessages([original], [truncated])[0]).toEqual(original);
  const { sequence, revision, detail, parent_message_id, ...live } = message(1);
  const updated = mergeMessages([original], [{ ...live, content: 'revised live' }])[0];
  expect(updated).toMatchObject({ sequence: 1, revision: 2, parent_message_id: 'parent', content: 'revised live', detail: original.detail });
});

it('retries a failed saved gap even when all previous older history was loaded', async () => {
  const { result, request } = setup();
  act(() => result.current.open('s', undefined, page([1, 2], null)));
  request.mockRejectedValueOnce(new Error('gap fixture')).mockResolvedValueOnce(page([3, 4], 'before-3'));
  await act(() => result.current.synchronize(page([5, 6], 'before-5')));
  expect(result.current.pagination.error).toBeTruthy();
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2]);
  await act(() => result.current.loadOlder());
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4, 5, 6]);
  expect(result.current.pagination.error).toBe('');
});

it('does not replace a newer draft with an older snapshot from the same run', () => {
  const latest = { ...message(1), provisional: true, state: 'draft' as const, run_id: 'r', delta_index: 5, content: 'complete prefix' };
  expect(mergeMessages([latest], [{ ...latest, delta_index: 3, content: 'old prefix' }])[0]).toEqual(latest);
});

it('serializes two saved synchronizations which were waiting behind an older page', async () => {
  const { result, request } = setup();
  let finishOlder!: (value: HistoryPage) => void, finishGap!: (value: HistoryPage) => void;
  request.mockImplementationOnce(() => new Promise(resolve => { finishOlder = resolve; }))
    .mockImplementationOnce(() => new Promise(resolve => { finishGap = resolve; }))
    .mockRejectedValue(new Error('Unexpected concurrent query'));
  let older!: Promise<void>, first!: Promise<void>, second!: Promise<void>;
  act(() => {
    older = result.current.loadOlder();
    first = result.current.synchronize(page([7, 8], 'before-7'));
    second = result.current.synchronize(page([9, 10], 'before-9'));
  });
  await act(async () => { finishOlder(page([1, 2], null)); await older; });
  await waitFor(() => expect(finishGap).toBeTypeOf('function'));
  const started = request.mock.calls.length;
  await act(async () => { finishGap(page([5, 6], 'before-5')); await Promise.all([first, second]); });
  expect(started).toBe(2);
  expect(result.current.messages.map(m => m.sequence)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
});
