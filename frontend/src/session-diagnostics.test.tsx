import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, WebApi } from './api';
import ContextPanel from './components/ContextPanel';
import SessionDiagnostics from './components/SessionDiagnostics';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('distinguishes an independent memory execution and its model purpose', async () => {
  const api = new WebApi('test');
  const item = { ...summary('memory-run'), mode: 'memory', origin_run_id: 'source-root-run' };
  vi.spyOn(api, 'diagnosticRuns').mockResolvedValue({ session_id: 's', runs: [item], next_before: null });
  const value = detail('memory-run');
  value.summary = item;
  value.events = [{ session_id: 's', run_id: 'memory-run', sequence: 2, event_type: 'model_call_started',
    source: null, data: { invocation_id: 'i', model_id: 'A', purpose: 'memory' } }];
  vi.spyOn(api, 'diagnosticEvents').mockResolvedValue(value);
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看运行 memory-run' }));
  expect((await screen.findAllByText('记忆整理')).length).toBe(2);
  expect(screen.getByText('用途')).toBeTruthy();
  expect(screen.getByText('来源运行 source-root-run')).toBeTruthy();
});

function summary(rid = 'r', sid = 's'): any {
  return { session_id: sid, run_id: rid, status: 'failed', started_at: '2026-10-20T00:00:00Z',
    duration_ms: 0, capture_state: 'closed', capture_incomplete: false, dropped_events: 0,
    http_attempts: { status: 'complete', observed_attempts: 3, response_headers: 2, unconfirmed: 1 } };
}
function list(rid = 'r', sid = 's', next: number | null = null): any {
  return { session_id: sid, runs: [summary(rid, sid)], next_before: next };
}
function detail(rid = 'r', sid = 's', next: number | null = null): any {
  return { session_id: sid, run_id: rid, summary: summary(rid, sid), next_after: next,
    events: [{ session_id: sid, run_id: rid, sequence: 7, event_type: 'model_http_attempt_finished',
      source: { agent_path: 'root/tester/id', agent_id: 'id', agent_run_id: 'child-run' },
      data: { invocation_id: 'i', attempt_id: 'a', status: 'response_headers', http_status: 200, response_headers_ms: 0 } },
    { session_id: sid, run_id: rid, sequence: 8, event_type: 'model_call_finished', source: null,
      data: { invocation_id: 'i', status: 'failed', duration_ms: null, body: 'PRIVATE_BODY' } }] };
}

it('opens the fourth tab lazily and cycles four keyboard targets independently of context errors', async () => {
  const api = new WebApi('test');
  const read = vi.spyOn(api, 'diagnosticRuns').mockResolvedValue(list());
  render(<ContextPanel loading={false} error="context unavailable" retry={vi.fn()} usageContent={null}
    diagnosticContent={<SessionDiagnostics api={api} sessionId="s" run={null} />} />);
  expect(read).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole('tab', { name: '当前预估' }), { key: 'End' });
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: '运行诊断' }));
  expect(await screen.findByRole('button', { name: '查看运行 r' })).toBeTruthy();
  expect(screen.queryByText('context unavailable')).toBeNull();
  fireEvent.keyDown(screen.getByRole('tab', { name: '运行诊断' }), { key: 'ArrowRight' });
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: '当前预估' }));
  fireEvent.keyDown(screen.getByRole('tab', { name: '当前预估' }), { key: 'ArrowLeft' });
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: '运行诊断' }));
});

it('paginates bounded lists and events, showing full-run HTTP counts on every page', async () => {
  const api = new WebApi('test');
  const runs = vi.spyOn(api, 'diagnosticRuns').mockResolvedValueOnce(list('r', 's', 4)).mockResolvedValue(list('older'));
  const first = detail('older', 's', 8);
  const second = { ...detail('older'), events: [{ session_id: 's', run_id: 'older', sequence: 9,
    event_type: 'tool_call_finished', source: null, data: { tool_name: 'read', tool_call_id: 't',
    boundary: 'dispatch', outcome: 'success', duration_ms: 1234 } }] };
  const events = vi.spyOn(api, 'diagnosticEvents').mockResolvedValueOnce(first).mockResolvedValue(second);
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  fireEvent.click(await screen.findByRole('button', { name: '更早运行' }));
  expect(await screen.findByRole('button', { name: '查看运行 older' })).toBeTruthy();
  expect(runs.mock.calls[1][1]).toBe(4);
  fireEvent.click(screen.getByRole('button', { name: '查看运行 older' }));
  expect(await screen.findByText('HTTP 200')).toBeTruthy();
  expect(screen.getAllByText('失败').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('root/tester/id')).toBeTruthy();
  expect(screen.getByText('child-run')).toBeTruthy();
  expect(screen.queryByText('PRIVATE_BODY')).toBeNull();
  expect(screen.getByText(/已记录 HTTP 尝试 3/)).toBeTruthy();
  expect(screen.getByText('耗时0 毫秒')).toBeTruthy();
  expect(screen.getByText('0 毫秒')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '下一页事件' }));
  expect(await screen.findByText('read')).toBeTruthy();
  expect(screen.getByText('1.234 秒')).toBeTruthy();
  expect(screen.queryByText('HTTP 200')).toBeNull();
  expect(screen.getByText(/已记录 HTTP 尝试 3/)).toBeTruthy();
  expect(events.mock.calls[1].slice(0, 3)).toEqual(['s', 'older', 8]);
  expect(screen.queryByText(/配对丢失/)).toBeNull();
});

it.each(['unavailable', 'partial', 'complete'])('distinguishes HTTP %s from truthful zero and old durations', async status => {
  const api = new WebApi('test');
  const value = list();
  value.runs[0] = { ...summary(), duration_ms: undefined, http_attempts: status === 'unavailable' ? undefined :
    { status, observed_attempts: 0, response_headers: 0, unconfirmed: 0 } };
  vi.spyOn(api, 'diagnosticRuns').mockResolvedValue(value);
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  await screen.findByRole('button', { name: '查看运行 r' });
  expect(screen.getByText(/耗时未提供/)).toBeTruthy();
  if (status === 'unavailable') {
    expect(screen.getByText('HTTP 采集不可用')).toBeTruthy();
    expect(screen.queryByText(/HTTP 尝试 0/)).toBeNull();
  } else {
    expect(screen.getByText(/已记录 HTTP 尝试 0/)).toBeTruthy();
    expect(!!screen.queryByText(/HTTP 采集不完整/)).toBe(status === 'partial');
  }
});

it('shows an empty archive and safe local retry without exposing the underlying error', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'diagnosticRuns').mockRejectedValueOnce(new Error('PRIVATE_SQL'))
    .mockResolvedValue({ session_id: 's', runs: [], next_before: null });
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByText('PRIVATE_SQL')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '刷新运行诊断' }));
  expect(await screen.findByText('暂无已保留的运行诊断。')).toBeTruthy();
  expect(screen.queryByText(/HTTP 尝试 0/)).toBeNull();
});

it('aborts a previous session and rejects late results', async () => {
  const api = new WebApi('test');
  let resolve!: (v: any) => void;
  const reads = vi.spyOn(api, 'diagnosticRuns').mockImplementationOnce(() => new Promise(r => { resolve = r; }))
    .mockResolvedValue(list('new', 'b'));
  const { rerender } = render(<SessionDiagnostics api={api} sessionId="a" run={null} />);
  rerender(<SessionDiagnostics api={api} sessionId="b" run={null} />);
  await screen.findByRole('button', { name: '查看运行 new' });
  await act(async () => resolve(list('PRIVATE_OLD', 'a')));
  expect(screen.queryByText('PRIVATE_OLD')).toBeNull();
  expect(reads.mock.calls[0][2]?.aborted).toBe(true);
});

it('aborts stale run reads after returning to the list and selecting another run', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'diagnosticRuns').mockResolvedValue({ session_id: 's', runs: [summary('one'), summary('two')], next_before: null });
  let resolve!: (v: any) => void;
  const events = vi.spyOn(api, 'diagnosticEvents').mockImplementationOnce(() => new Promise(r => { resolve = r; }))
    .mockResolvedValue({ ...detail('two'), events: [] });
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看运行 one' }));
  fireEvent.click(screen.getByRole('button', { name: '返回运行列表' }));
  fireEvent.click(await screen.findByRole('button', { name: '查看运行 two' }));
  await screen.findByText('暂无本页事件。');
  await act(async () => resolve(detail('one')));
  expect(screen.queryByText('HTTP 200')).toBeNull();
  expect(events.mock.calls[0][3]?.aborted).toBe(true);
});

it.each(['session', 'summary', 'event'])('refuses a mismatched %s identity', async scope => {
  const api = new WebApi('test');
  vi.spyOn(api, 'diagnosticRuns').mockResolvedValue(list());
  const value = detail();
  if (scope === 'session') value.session_id = 'foreign';
  if (scope === 'summary') value.summary.run_id = 'foreign';
  if (scope === 'event') value.events[0].session_id = 'foreign';
  vi.spyOn(api, 'diagnosticEvents').mockResolvedValue(value);
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看运行 r' }));
  expect(await screen.findByRole('alert')).toBeTruthy();
  expect(screen.queryByText('HTTP 200')).toBeNull();
});

it('shows missing retained diagnostics without inventing an empty successful run', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'diagnosticRuns').mockResolvedValue(list());
  vi.spyOn(api, 'diagnosticEvents').mockRejectedValue(new ApiError('diagnostic_run_not_found'));
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看运行 r' }));
  expect(await screen.findByText(/该运行诊断不可用/)).toBeTruthy();
  expect(screen.queryByText('暂无本页事件。')).toBeNull();
});

it('refreshes selected run once on this root completion and ignores other roots and token updates', async () => {
  const api = new WebApi('test');
  const runs = vi.spyOn(api, 'diagnosticRuns').mockResolvedValue(list());
  const events = vi.spyOn(api, 'diagnosticEvents').mockResolvedValue(detail());
  const run: any = { session_id: 's', run_id: 'new', done: false };
  const { rerender } = render(<SessionDiagnostics api={api} sessionId="s" run={run} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看运行 r' }));
  await screen.findByText('HTTP 200');
  rerender(<SessionDiagnostics api={api} sessionId="s" run={{ ...run, after: 10 }} />);
  rerender(<SessionDiagnostics api={api} sessionId="s" run={{ ...run, session_id: 'other', done: true }} />);
  expect(events).toHaveBeenCalledTimes(1);
  rerender(<SessionDiagnostics api={api} sessionId="s" run={{ ...run, done: true }} />);
  await waitFor(() => expect(events).toHaveBeenCalledTimes(2));
  expect(events.mock.calls[1].slice(0, 3)).toEqual(['s', 'r', 0]);
  rerender(<SessionDiagnostics api={api} sessionId="s" run={{ ...run, done: true }} />);
  expect(events).toHaveBeenCalledTimes(2);
  expect(runs).toHaveBeenCalledTimes(1);
});

it('encodes diagnostic identities and cursors and forwards authentication and cancellation', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
  vi.stubGlobal('fetch', fetcher);
  const api = new WebApi('test');
  const controller = new AbortController();
  await api.diagnosticRuns('session/id', 7, controller.signal);
  await api.diagnosticEvents('session/id', 'run/id', 9, controller.signal);
  expect(fetcher.mock.calls[0][0]).toBe('/api/sessions/session%2Fid/diagnostics?before=7');
  expect(fetcher.mock.calls[1][0]).toBe('/api/sessions/session%2Fid/diagnostics/run%2Fid?after=9');
  for (const [, init] of fetcher.mock.calls) {
    expect(init.signal).toBe(controller.signal);
    expect(init.headers.Authorization).toBe('Bearer test');
  }
});

it('preserves identifier text even when it matches a status or purpose name and escapes markup', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'diagnosticRuns').mockResolvedValue(list());
  const value = detail();
  value.events[0].data = { model_id: 'failed', invocation_id: 'summary', tool_name: '<img src=x onerror=alert(1)>' };
  vi.spyOn(api, 'diagnosticEvents').mockResolvedValue(value);
  render(<SessionDiagnostics api={api} sessionId="s" run={null} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看运行 r' }));
  expect(await screen.findByText('failed')).toBeTruthy();
  expect(screen.getByText('summary')).toBeTruthy();
  expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy();
  expect(document.querySelector('img')).toBeNull();
});
