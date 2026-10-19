import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import type { RunView } from './types';
import { subscribeRun } from './runSubscription';

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
const live: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [],
  done: false, incomplete: false, last_sequence: 5, manual_review_required: false };
function setup() {
  vi.useFakeTimers();
  const api = new WebApi('subscription-fixture');
  const run = vi.spyOn(api, 'run').mockResolvedValue(live);
  const events = vi.spyOn(api, 'events').mockRejectedValue(new TypeError('network unavailable'));
  const controller = new AbortController();
  const synchronize = vi.fn().mockResolvedValue(undefined), update = vi.fn(), reconnecting = vi.fn();
  const options = { api, runId: 'r', sessionId: 's', signal: controller.signal, synchronize, update, reconnecting };
  return { api, run, events, controller, synchronize, update, reconnecting, options };
}

it('backs off, synchronizes and resumes from a snapshot without sending execution requests', async () => {
  const { api, run, events, synchronize, reconnecting, options } = setup();
  run.mockResolvedValueOnce(live).mockResolvedValueOnce({ ...live, last_sequence: 6 })
    .mockResolvedValueOnce({ ...live, status: 'completed', done: true, last_sequence: 8 });
  events.mockRejectedValueOnce(new TypeError('offline')).mockImplementationOnce(async (_id, _after, _signal, receive) => {
    receive({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'r', sequence: 7,
      event_type: 'run_finished', data: { status: 'completed' } } });
  });
  const start = vi.spyOn(api, 'start'), approve = vi.spyOn(api, 'approve'), control = vi.spyOn(api, 'control');
  const pending = subscribeRun(options);
  await vi.advanceTimersByTimeAsync(0);
  expect(reconnecting).toHaveBeenCalledWith(1);
  expect(synchronize).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(250);
  expect((await pending)?.status).toBe('completed');
  expect(synchronize).toHaveBeenCalledTimes(1);
  expect(events.mock.calls.map(call => call.slice(0, 2))).toEqual([['r', 5], ['r', 6]]);
  expect(start).not.toHaveBeenCalled(); expect(approve).not.toHaveBeenCalled(); expect(control).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

it('stops after three retries and preserves the original failure', async () => {
  const { events, reconnecting, options } = setup();
  const error = new TypeError('offline'); events.mockRejectedValue(error);
  const pending = subscribeRun(options);
  const rejected = expect(pending).rejects.toBe(error);
  await vi.advanceTimersByTimeAsync(4250);
  await rejected;
  expect(events).toHaveBeenCalledTimes(4);
  expect(reconnecting.mock.calls.map(call => call[0])).toEqual([1, 2, 3]);
});

it('cancels a backoff when leaving the selected session without reading or rendering again', async () => {
  const { controller, run, synchronize, update, options } = setup();
  const pending = subscribeRun(options);
  await vi.advanceTimersByTimeAsync(0);
  controller.abort();
  expect(await pending).toBeUndefined();
  expect(run).toHaveBeenCalledTimes(1); expect(synchronize).not.toHaveBeenCalled();
  expect(update).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
});

it('does not retry authorization failures or accept another run identity', async () => {
  const { run, reconnecting, options } = setup();
  const error = new ApiError('unauthorized'); run.mockRejectedValueOnce(error);
  await expect(subscribeRun(options)).rejects.toBe(error);
  expect(reconnecting).not.toHaveBeenCalled();
  run.mockResolvedValueOnce({ ...live, session_id: 'other' });
  await expect(subscribeRun(options)).rejects.toThrow('invalid_event');
  expect(reconnecting).not.toHaveBeenCalled();
});

it('does not subscribe again when synchronization finds a terminal snapshot', async () => {
  const { run, events, synchronize, options } = setup();
  run.mockResolvedValueOnce(live).mockResolvedValueOnce({ ...live, status: 'interrupted', done: true });
  const pending = subscribeRun(options);
  await vi.advanceTimersByTimeAsync(250);
  expect((await pending)?.status).toBe('interrupted');
  expect(events).toHaveBeenCalledTimes(1); expect(synchronize).toHaveBeenCalledTimes(1);
});

it('recovers a frame gap from a completed snapshot without replaying execution', async () => {
  const { api, run, events, synchronize, update, options } = setup();
  const complete = { ...live, status: 'completed' as const, done: true, last_sequence: 9, messages: [
    { id: 'large', type: 'ai', content: 'saved preview', provisional: false, state: 'complete' as const,
      content_truncated: true, sequence: 2, revision: 1, detail: { sequence: 2, revision: 1 } },
  ] };
  run.mockResolvedValueOnce(live).mockResolvedValueOnce(complete);
  events.mockImplementationOnce(async (_id, _after, _signal, receive) => {
    receive({ event: 'resync_required', data: { code: 'resync_required' } });
  });
  const start = vi.spyOn(api, 'start'), approve = vi.spyOn(api, 'approve'), control = vi.spyOn(api, 'control');
  const pending = subscribeRun(options);
  await vi.advanceTimersByTimeAsync(250);
  expect(await pending).toEqual(complete);
  expect(synchronize).toHaveBeenCalledTimes(1);
  expect(events).toHaveBeenCalledTimes(1);
  expect(update).toHaveBeenLastCalledWith(complete);
  expect(start).not.toHaveBeenCalled(); expect(approve).not.toHaveBeenCalled(); expect(control).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
