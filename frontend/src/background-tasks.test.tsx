import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import { useBackgroundTasks } from './useBackgroundTasks';
import BackgroundTasks from './components/BackgroundTasks';
import type { BackgroundTask, BackgroundTaskOutput } from './backgroundTaskTypes';
import { backgroundTaskPollMs } from './uiSettings';

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });
const task: BackgroundTask = { task_id: 'a'.repeat(32), session_id: 'session-one', workspace_root: '/workspace',
  origin_run_id: 'run', tool_call_id: 'call', shell: 'bash', command_preview: 'start service', status: 'running',
  created_at: '2026-10-21T10:00:00Z', started_at: null, finished_at: null, max_execution_seconds: null,
  exit_code: null, termination: null, output_complete: false, cleanup_confirmed: false, error_type: null };
const detail: BackgroundTaskOutput = { ...task, output: '<script>untrusted()</script>', truncated: false,
  observed_raw_bytes: 30, observed_text_bytes: 30, lossy: false, archive_paths: { text: 'naiyue-agent-output/task.txt' },
  archive_saved_bytes: 30, archive_complete: true, archive_reason: null, output_available: true };
const page = (items = [task], revision = 'one', terminal_revision = 0) => ({ items, revision, terminal_revision,
  active_count: items.filter(row => row.status === 'running').length, next_before: null as string | null });
function client() {
  const api = new WebApi('test');
  vi.spyOn(api, 'backgroundTasks').mockResolvedValue(page());
  vi.spyOn(api, 'backgroundTaskOutput').mockResolvedValue(detail);
  vi.spyOn(api, 'stopBackgroundTask').mockResolvedValue({ ...task, status: 'finished', cleanup_confirmed: true });
  return api;
}

it('keeps polling metadata without reading output or calling a model while closed', async () => {
  vi.useFakeTimers();
  const api = client();
  const start = vi.spyOn(api, 'start');
  const { result, unmount } = renderHook(() => useBackgroundTasks(api, false));
  await act(async () => {});
  expect(result.current.items).toHaveLength(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(backgroundTaskPollMs); });
  expect(api.backgroundTasks).toHaveBeenCalledTimes(2);
  expect(api.backgroundTaskOutput).not.toHaveBeenCalled();
  expect(start).not.toHaveBeenCalled();
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(backgroundTaskPollMs * 2); });
  expect(api.backgroundTasks).toHaveBeenCalledTimes(2);
});

it('renders literal output, can stop, and retains output when stop is unconfirmed', async () => {
  const api = client();
  vi.mocked(api.stopBackgroundTask).mockRejectedValue(new ApiError('task_cleanup_unconfirmed'));
  function Panel() { const state = useBackgroundTasks(api, true); return <BackgroundTasks state={state} />; }
  const view = render(<Panel />);
  fireEvent.click(await screen.findByRole('button', { name: /start service/ }));
  expect(await screen.findByText(detail.output)).toBeTruthy();
  expect(view.container.querySelector('script')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '停止此后台任务' }));
  await screen.findByText(/进程清理尚未确认/);
  expect(api.stopBackgroundTask).toHaveBeenCalledTimes(1);
  expect(screen.getByText(detail.output)).toBeTruthy();
});

it('cancels hidden reads, resumes when visible, and suppresses a stale selected output', async () => {
  const api = client();
  let resolve!: (value: BackgroundTaskOutput) => void;
  vi.mocked(api.backgroundTaskOutput).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const { result } = renderHook(() => useBackgroundTasks(api, true));
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  act(() => result.current.select(task.task_id));
  await waitFor(() => expect(api.backgroundTaskOutput).toHaveBeenCalledTimes(1));
  const signal = vi.mocked(api.backgroundTaskOutput).mock.calls[0][1]!;
  act(() => result.current.select('b'.repeat(32)));
  expect(signal.aborted).toBe(true);
  await act(async () => resolve({ ...detail, output: 'STALE' }));
  await waitFor(() => expect(result.current.output?.output).not.toBe('STALE'));
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(vi.mocked(api.backgroundTasks).mock.calls[0][1]?.aborted).toBe(true);
  const count = vi.mocked(api.backgroundTasks).mock.calls.length;
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  await waitFor(() => expect(api.backgroundTasks).toHaveBeenCalledTimes(count + 1));
});

it('announces a new terminal revision after baseline, independently of the chat', async () => {
  const api = client();
  const { result } = renderHook(() => useBackgroundTasks(api, false));
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  expect(result.current.notice).toBe('');
  vi.mocked(api.backgroundTasks).mockResolvedValue(page([{ ...task, status: 'finished' }], 'two', 1));
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.notice).toMatch(/已结束或需核对/));
  expect(result.current.activeCount).toBe(0);
});

it('supports bounded load-more and exposes list failure without inventing zero', async () => {
  const api = client();
  vi.mocked(api.backgroundTasks).mockResolvedValueOnce({ ...page(), next_before: task.task_id })
    .mockResolvedValueOnce(page([{ ...task, task_id: 'b'.repeat(32) }]));
  const { result } = renderHook(() => useBackgroundTasks(api, false));
  await waitFor(() => expect(result.current.nextBefore).toBe(task.task_id));
  await act(async () => result.current.loadMore());
  expect(result.current.items).toHaveLength(2);
  vi.mocked(api.backgroundTasks).mockRejectedValue(new Error('offline'));
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.error).toBeTruthy());
  expect(result.current.activeCount).toBeUndefined();
});

it('encodes task identities and passes cancellation through the actual HTTP API', async () => {
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(page())));
  const api = new WebApi('test'); const controller = new AbortController();
  await api.backgroundTasks('cursor /?', controller.signal);
  expect(fetcher.mock.calls[0][0]).toBe('/api/background-tasks?before=cursor+%2F%3F');
  expect(fetcher.mock.calls[0][1]?.signal).toBe(controller.signal);
});

it('does not label an actively written archive as incomplete', async () => {
  const api = client();
  vi.mocked(api.backgroundTaskOutput).mockResolvedValue({ ...detail, archive_complete: false });
  function Panel() { const state = useBackgroundTasks(api, true); return <BackgroundTasks state={state} />; }
  render(<Panel />);
  fireEvent.click(await screen.findByRole('button', { name: /start service/ }));
  await screen.findByText(detail.output);
  expect(screen.queryByText(/归档不完整/)).toBeNull();
});

it('keeps a delayed stop failure attached to its task after selection changes', async () => {
  const api = client();
  let reject!: (value: unknown) => void;
  vi.mocked(api.stopBackgroundTask).mockImplementation(() => new Promise((_done, fail) => { reject = fail; }));
  const { result } = renderHook(() => useBackgroundTasks(api, true));
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  act(() => result.current.select(task.task_id));
  let pending!: Promise<void>;
  act(() => { pending = result.current.stop(task.task_id); result.current.select('b'.repeat(32)); });
  await act(async () => { reject(new ApiError('task_cleanup_unconfirmed')); await pending; });
  expect(result.current.stopError).toBe('');
  act(() => result.current.select(task.task_id));
  expect(result.current.stopError).toMatch(/进程清理尚未确认/);
});
