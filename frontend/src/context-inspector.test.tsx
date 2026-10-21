import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { WebApi } from './api';
import ContextPanel, { ContextTrigger } from './components/ContextPanel';
import { useContextView } from './useContext';
import type { ContextView } from './contextTypes';
import { applyEvent } from './events';

afterEach(cleanup);
const estimate = { status: 'available', estimated_input_tokens: 34000, context_window_tokens: 128000,
  max_output_tokens: 16384, context_window_source: 'config', max_output_source: 'default',
  compaction_reserve_tokens: 24000, compaction_reserve_source: 'config', trigger_threshold_tokens: 104000,
  estimator: 'framework_approximate', image_count: 0, groups: [{ id: 'system', tokens: 6000, count: 1,
    children: [{ id: 'project', source: 'AGENTS.md', tokens: 2100 }] }, { id: 'tool_results', tokens: 28000, count: 3 }] } as const;
const view: ContextView = { session_id: 's', current: { basis: 'next_request', model_id: 'A', estimate: structuredClone(estimate) },
  latest_request: { version: 1, model_call_id: 'call', model_id: 'A', run_id: 'r', recorded_at: '2026-10-17T12:00:00Z',
    status: 'returned', estimate: { ...structuredClone(estimate), estimated_input_tokens: 32800 },
    usage: { input_tokens: 33280, output_tokens: 1600, cache_read_tokens: 0, cache_write_tokens: 1200 } } };

it('warns at the configured compaction threshold using the current estimate', () => {
  const { rerender } = render(<ContextTrigger value={{ ...view, current: { ...view.current,
    estimate: { ...estimate, estimated_input_tokens: 110000 } } }} open={false} disabled={false} toggle={vi.fn()} triggerRef={{ current: null }} />);
  expect(screen.getByRole('button').getAttribute('data-threshold')).toBe('reached');
  expect(screen.getByRole('button').title).toContain('压缩阈值');
  rerender(<ContextTrigger value={view} open={false} disabled={false} toggle={vi.fn()} triggerRef={{ current: null }} />);
  expect(screen.getByRole('button').getAttribute('data-threshold')).toBeNull();
});

it('keeps a usable total and omits unavailable composition without an extra notice', () => {
  const partial: ContextView = { ...view, current: { ...view.current,
    estimate: { ...estimate, groups: [], groups_status: 'unavailable' } } };
  render(<ContextPanel usageContent={null} value={partial} loading={false} error="" retry={vi.fn()} close={vi.fn()} />);
  expect(screen.getByText('输入预估 34,000')).toBeTruthy();
  expect(screen.queryByText('输入构成')).toBeNull();
  expect(screen.queryByText(/当前预估暂不可用/)).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('separates estimates and provider usage, preserving zero and missing values', () => {
  const { rerender } = render(<ContextPanel usageContent={null} value={view} loading={false} error="" retry={vi.fn()} close={vi.fn()} />);
  expect(screen.getByText('输入预估 34,000')).toBeTruthy();
  fireEvent.keyDown(screen.getByRole('tab', { name: '当前预估' }), { key: 'ArrowRight' });
  expect(document.activeElement).toBe(screen.getByRole('tab', { name: '最近请求' }));
  expect(screen.getByText('33,280')).toBeTruthy();
  expect(screen.getByText('0')).toBeTruthy();
  expect(screen.getByText('1,200')).toBeTruthy();
  expect(screen.getByText(/包含缓存读取与写入/)).toBeTruthy();
  expect(screen.getByText('↳ 其中缓存写入')).toBeTruthy();
  rerender(<ContextPanel usageContent={null} value={{ ...view, latest_request: { ...view.latest_request!, usage: null } }}
    loading={false} error="" retry={vi.fn()} close={vi.fn()} />);
  expect(screen.getAllByText('未提供')).toHaveLength(4);
  expect(screen.queryByText('33,280')).toBeNull();
});

it('discards a previous session response and supports retry', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  let finish!: (value: ContextView) => void;
  vi.spyOn(api, 'context').mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockRejectedValueOnce(new Error('network')).mockResolvedValue({ ...view, session_id: 'new' });
  function Harness({ id }: { id: string }) {
    const state = useContextView(api, { session_id: id, model_id: 'A', active_tools: [] } as any, null);
    return <div>{state.value?.session_id}<span>{state.error}</span><button onClick={state.retry}>重试</button></div>;
  }
  const { rerender } = render(<Harness id="s" />);
  rerender(<Harness id="new" />);
  await screen.findByText(/上下文读取失败/);
  await act(async () => finish(view));
  expect(screen.queryByText('s')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('new')).toBeTruthy();
});

it('places the context entry in the send group and switches between sidebars without sending', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  const session = { session_id: 's', model_id: 'A', workspace_root: null, state: 'ready', updated_at: '',
    last_run_status: null, messages: [], busy: false, can_run: true, has_compaction: false, active_tools: [],
    permission_mode: 'Default', active_run_id: null, latest_run_id: null, manual_review_required: false,
    pending_approval: false, live: null } as const;
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessions').mockResolvedValue([session] as any);
  vi.spyOn(api, 'session').mockResolvedValue(session as any);
  vi.spyOn(api, 'context').mockResolvedValue(view);
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], permission_modes: ['Default'],
    default_permission_mode: 'Default', default_active_tools: [] });
  vi.spyOn(api, 'subagents').mockResolvedValue([{ agent_id: 'saved', agent_path: 'root/tester/saved',
    agent: 'tester', task: 'inspect', tool_call_id: 'c', status: 'completed' }]);
  const start = vi.spyOn(api, 'start');
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: /Invalid Date/ }));
  const trigger = await screen.findByRole('button', { name: /查看上下文构成，预估占用26.6%/ });
  const send = screen.getByRole('button', { name: '发送消息' });
  expect(trigger.parentElement).toBe(send.parentElement);
  expect(trigger.closest('header')).toBeNull();
  fireEvent.click(trigger);
  const panel = await screen.findByRole('complementary', { name: '上下文' });
  expect(within(panel).getByRole('tab', { name: '当前预估' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '打开子智能体侧栏' }));
  expect(screen.queryByRole('complementary', { name: '上下文' })).toBeNull();
  expect(await screen.findByRole('complementary', { name: '子智能体' })).toBeTruthy();
  fireEvent.click(trigger);
  expect(screen.queryByRole('complementary', { name: '子智能体' })).toBeNull();
  fireEvent.keyDown(screen.getByRole('complementary', { name: '上下文' }), { key: 'Escape' });
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  expect(start).not.toHaveBeenCalled();
});

it('accepts omitted statistics without losing the contiguous main event sequence', () => {
  const run = { session_id: 's', run_id: 'r', status: 'running', messages: [], last_sequence: 3,
    incomplete: false, done: false, manual_review_required: false, context_snapshot: view.latest_request } as any;
  const result = applyEvent(run, { version: 1, session_id: 's', run_id: 'r', sequence: 4,
    event_type: 'model_context_snapshot', data: { model_call_id: 'call', snapshot_json: null } });
  expect(result.last_sequence).toBe(4);
  expect(result.incomplete).toBe(false);
  expect(result.context_snapshot).toBeNull();
});
