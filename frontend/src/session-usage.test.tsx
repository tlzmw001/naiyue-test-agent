import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ContextPanel from './components/ContextPanel';
import SessionUsage from './components/SessionUsage';
import { WebApi } from './api';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const metric = (sum: string | null, unknown = 0) => ({ known_sum: sum, reported_calls: sum === null ? 0 : 1, unknown_calls: unknown });
function view(sid = 's', sum = '3500'): any {
  return { session_id: sid, as_of: '2026-10-18T12:00:00Z', recorded_calls: 4,
    metrics: { input_tokens: metric(sum, 1), output_tokens: metric('350', 1), cache_read_tokens: metric(null, 4), cache_write_tokens: metric('0', 2) },
    status_counts: { returned: 3, failed: 1, cancelled: 0, started: 0, unconfirmed: 0 },
    usage_status_counts: { final: 3, partial: 0, unavailable: 1 }, activities: [], scopes: [],
    models: { items: [], next_after: null, unattributed: { recorded_calls: 0 } },
    capture: { recorded_runs: 1, incomplete_runs: 0, dropped_events: '0' } };
}

it('loads only when cumulative tab is opened and cycles all three tabs', async () => {
  const api = new WebApi('test');
  const usage = vi.spyOn(api, 'usage').mockResolvedValue(view());
  render(<ContextPanel loading={false} error="" retry={vi.fn()} close={vi.fn()}
    usageContent={<SessionUsage api={api} sessionId="s" run={null} />} />);
  expect(usage).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole('tab', { name: '当前预估' }), { key: 'End' });
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: '会话累计' }));
  expect(await screen.findByText('3,500')).toBeTruthy();
  expect(screen.getByText('0')).toBeTruthy();
  expect(screen.getByText('未提供')).toBeTruthy();
  expect(usage).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(screen.getByRole('tab', { name: '会话累计' }), { key: 'ArrowRight' });
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: '当前预估' }));
});

it('keeps cumulative available when context reads fail and preserves large integers', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'usage').mockResolvedValue(view('s', '9223372036854775808'));
  render(<ContextPanel loading={false} error="context unavailable" retry={vi.fn()} close={vi.fn()}
    usageContent={<SessionUsage api={api} sessionId="s" run={null} />} />);
  fireEvent.click(screen.getByRole('tab', { name: '会话累计' }));
  expect(await screen.findByText('9,223,372,036,854,775,808')).toBeTruthy();
  expect(screen.queryByText('context unavailable')).toBeNull();
});

it('isolates a stale session response, retries a failure and aborts old reads', async () => {
  const api = new WebApi('test');
  let resolve!: (value: any) => void;
  const usage = vi.spyOn(api, 'usage').mockImplementationOnce(() => new Promise(r => { resolve = r; }))
    .mockRejectedValueOnce(new Error('PRIVATE_ERROR')).mockResolvedValue(view('b', '900'));
  const { rerender } = render(<SessionUsage api={api} sessionId="a" run={null} />);
  rerender(<SessionUsage api={api} sessionId="b" run={null} />);
  expect(await screen.findByText(/累计用量读取失败/)).toBeTruthy();
  await act(async () => resolve(view('a')));
  expect(screen.queryByText('3,500')).toBeNull();
  expect(usage.mock.calls[0][2]?.aborted).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '刷新累计用量' }));
  expect(await screen.findByText('900')).toBeTruthy();
  expect(screen.queryByText('PRIVATE_ERROR')).toBeNull();
});

it('refreshes on this root completion and ignores token updates or another root', async () => {
  const api = new WebApi('test');
  const usage = vi.spyOn(api, 'usage').mockResolvedValueOnce(view()).mockResolvedValue(view('s', '900'));
  const run: any = { session_id: 's', run_id: 'r', done: false };
  const { rerender } = render(<SessionUsage api={api} sessionId="s" run={run} />);
  await screen.findByText('3,500');
  rerender(<SessionUsage api={api} sessionId="s" run={{ ...run, after: 200 } as any} />);
  rerender(<SessionUsage api={api} sessionId="s" run={{ ...run, session_id: 'other', done: true }} />);
  expect(usage).toHaveBeenCalledTimes(1);
  rerender(<SessionUsage api={api} sessionId="s" run={{ ...run, done: true }} />);
  expect(await screen.findByText('900')).toBeTruthy();
  expect(usage).toHaveBeenCalledTimes(2);
  rerender(<SessionUsage api={api} sessionId="s" run={{ ...run, done: true }} />);
  expect(usage).toHaveBeenCalledTimes(2);
});

it('keeps totals across model pages and resets the page after completion', async () => {
  const api = new WebApi('test');
  const first = view();
  first.models.items = [{ recorded_calls: 1, metrics: first.metrics, model_id: 'A' }];
  first.models.next_after = 'A';
  const second = view();
  second.models.items = [{ recorded_calls: 1, metrics: second.metrics, model_id: 'B' }];
  const usage = vi.spyOn(api, 'usage').mockResolvedValueOnce(first).mockResolvedValueOnce(second).mockResolvedValue(first);
  const run: any = { session_id: 's', run_id: 'r', done: false };
  const { rerender } = render(<SessionUsage api={api} sessionId="s" run={run} />);
  await screen.findByText('3,500');
  fireEvent.click(screen.getByText('按模型查看'));
  fireEvent.click(screen.getByRole('button', { name: '下一页模型' }));
  expect(await screen.findByText('B')).toBeTruthy();
  expect(screen.getByText('按模型查看').closest('details')?.open).toBe(true);
  expect(screen.getByText('3,500')).toBeTruthy();
  expect(usage.mock.calls[1][1]).toBe('A');
  rerender(<SessionUsage api={api} sessionId="s" run={{ ...run, done: true }} />);
  await waitFor(() => expect(usage).toHaveBeenCalledTimes(3));
  expect(usage.mock.calls[2][1]).toBeUndefined();
  expect(await screen.findByRole('button', { name: '下一页模型' })).toBeTruthy();
});

it('shows an empty archive with a capture gap without inventing token values', async () => {
  const api = new WebApi('test');
  const empty = view();
  empty.recorded_calls = 0;
  empty.capture = { recorded_runs: 1, incomplete_runs: 1, dropped_events: '3' };
  vi.spyOn(api, 'usage').mockResolvedValue(empty);
  render(<SessionUsage api={api} sessionId="s" run={null} />);
  expect(await screen.findByText('暂无已保存的调用用量记录。')).toBeTruthy();
  expect(screen.getByRole('note').textContent).toContain('已知丢失 3 条事实');
  expect(screen.queryByText('3,500')).toBeNull();
});

it('keeps partial, unconfirmed and unattributed facts visible', async () => {
  const api = new WebApi('test');
  const partial = view();
  partial.usage_status_counts.partial = 1;
  partial.status_counts.unconfirmed = 1;
  partial.models.unattributed = { recorded_calls: 1, metrics: partial.metrics };
  vi.spyOn(api, 'usage').mockResolvedValue(partial);
  render(<SessionUsage api={api} sessionId="s" run={null} />);
  expect(await screen.findByText(/部分用量报告/)).toBeTruthy();
  expect(screen.getByText(/未确认 1 次/)).toBeTruthy();
  expect(screen.getByText('未归属模型')).toBeTruthy();
});

it('encodes the model cursor and forwards cancellation to the HTTP request', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => view() });
  vi.stubGlobal('fetch', fetcher);
  const controller = new AbortController();
  await new WebApi('test').usage('session/id', 'gateway/model:v2', controller.signal);
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe('/api/sessions/session%2Fid/usage?after_model=gateway%2Fmodel%3Av2');
  expect(init.signal).toBe(controller.signal);
});
