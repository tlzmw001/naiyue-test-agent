import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import SessionUsage from './components/SessionUsage';
import { WebApi } from './api';

afterEach(cleanup);

function view(duration: any): any {
  const metrics = Object.fromEntries(['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens']
    .map(name => [name, { known_sum: '0', reported_calls: 1, unknown_calls: 0 }]));
  const row = { recorded_calls: 2, duration, metrics, status_counts: { returned: 2 }, usage_status_counts: {} };
  return { ...row, session_id: 's', as_of: '2026-10-20T00:00:00Z',
    activities: [{ ...row, id: 'summary' }], scopes: [{ ...row, id: 'root' }],
    models: { items: [{ ...row, model_id: 'A' }], next_after: null, unattributed: { recorded_calls: 0 } },
    capture: { recorded_runs: 1, incomplete_runs: 0, dropped_events: '0' } };
}

it.each([
  [{ known_sum_ms: '9007199254740993001', reported_calls: 1, unknown_calls: 1 }, '9007199254740993.001 秒'],
  [{ known_sum_ms: '0', reported_calls: 2, unknown_calls: 0 }, '0 毫秒'],
  [undefined, '未提供'],
])('shows duration in totals and every existing breakdown without losing precision', async (duration, label) => {
  const api = new WebApi('test');
  vi.spyOn(api, 'usage').mockResolvedValue(view(duration));
  render(<SessionUsage api={api} sessionId="s" run={null} />);
  expect(await screen.findByText('已记录模型调用耗时')).toBeTruthy();
  expect(screen.getAllByText(label, { exact: false }).length).toBeGreaterThanOrEqual(4);
  expect(screen.getByText(/并发调用耗时之和/)).toBeTruthy();
  if (duration?.unknown_calls) expect(screen.getAllByText(/另有 1 次未提供/)).toHaveLength(4);
});
