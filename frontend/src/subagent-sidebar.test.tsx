import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { WebApi } from './api';

afterEach(cleanup);
it('opens children only on request, keeps approval in main and links delegation to the exact detail', async () => {
  const api = new WebApi('test');
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  const record = { agent_id: 'abcdef123', agent_path: 'root/tester/abcdef123', agent: 'tester',
    task: 'inspect', tool_call_id: 'outer', status: 'awaiting_approval' as const };
  const source = { ...record, agent_run_id: 'child', parent_run_id: 'r', parent_tool_call_id: 'outer' };
  const messages = [{ id: 'parent', type: 'ai', content: 'delegating', provisional: false, state: 'complete' as const,
    tool_calls: [{ id: 'outer', name: 'subagent', args: { agent: 'tester', task: 'inspect' } }] }];
  const child = { source, status: 'awaiting_approval' as const, tool_states: {}, messages: [
    { id: 'child-ai', type: 'ai', content: 'CHILD_PUBLIC_TEXT', provisional: false, state: 'complete' as const } ] };
  const run = { session_id: 's', run_id: 'r', status: 'running' as const, messages, last_sequence: 0,
    incomplete: false, done: false, manual_review_required: false, agents: { [record.agent_path]: child } };
  const row = { session_id: 's', model_id: 'A', workspace_root: null, state: 'running', updated_at: '2026-10-17T00:00:00Z', last_run_status: null };
  const batch = { approval_batch_id: 'b', kind: 'live_child' as const, agent_path: record.agent_path, agent: 'tester', origin_run_id: 'r',
    items: [{ approval_id: 'a', tool_call_id: 'inner', tool_name: 'bash', target: 'pytest' }] };
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] });
  vi.spyOn(api, 'sessions').mockResolvedValue([row]);
  vi.spyOn(api, 'session').mockResolvedValue({ ...row, messages, busy: true, can_run: false, active_tools: [], permission_mode: 'Default',
    has_compaction: false, active_run_id: 'r', latest_run_id: 'r', manual_review_required: false,
    pending_approval: true, approval_batches: [batch], live: run });
  vi.spyOn(api, 'events').mockImplementation((_id, _after, signal) => new Promise(resolve =>
    signal.addEventListener('abort', () => resolve())));
  vi.spyOn(api, 'subagents').mockResolvedValue([record]);
  vi.spyOn(api, 'subagent').mockResolvedValue({ ...record, messages: child.messages, available: true,
    has_compaction: false, tool_states: {} });
  const approve = vi.spyOn(api, 'approve').mockResolvedValue({ kind: 'live_child', accepted: true, session_id: 's', run_id: 'r' });
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: /A · 未完成/ }));
  await screen.findByRole('region', { name: '工具审批' });
  expect(screen.queryByRole('complementary', { name: '子智能体' })).toBeNull();
  expect(screen.queryByText('CHILD_PUBLIC_TEXT')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '查看子智能体 tester · abcdef' }));
  const panel = await screen.findByRole('complementary', { name: '子智能体' });
  await within(panel).findByText('CHILD_PUBLIC_TEXT');
  expect(within(panel).queryByText(/tester/)).toBeNull();
  expect(within(panel).queryByRole('radio')).toBeNull();
  const main = screen.getByRole('main');
  fireEvent.click(within(main).getByRole('radio', { name: '批准 1' }));
  fireEvent.click(within(main).getByRole('button', { name: '提交审批并继续' }));
  await waitFor(() => expect(approve).toHaveBeenCalledWith('s', batch, [{ approval_id: 'a', decision: 'approve' }], []));
  expect(api.events).toHaveBeenCalledTimes(1);
});
