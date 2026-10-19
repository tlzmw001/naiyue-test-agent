import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
import { WebApi } from './api';
import { applyEvent } from './events';
import type { RunView } from './types';
import type { SseFrame } from './sse';

// jsdom cannot start browser workers; retain real coloring while the dedicated
// Conversation.highlight suite verifies the worker lifecycle and failures.
vi.mock('./code-highlight-client', async () => {
  const { highlightCode } = await import('./code-highlight');
  return { requestHighlight: async (code: string, language: string) => highlightCode({ id: 0, code, language }).parts };
});

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
    { id: 'child-ai', type: 'ai', content: 'CHILD_PUBLIC_TEXT\n\n| 检查项 |\n| --- |\n| ~~旧说明~~ |\n\n- [x] 已检查', provisional: false, state: 'complete' as const } ] };
  const run = { session_id: 's', run_id: 'r', status: 'running' as const, messages, last_sequence: 0,
    incomplete: false, done: false, manual_review_required: false, agents: { [record.agent_path]: child } };
  const row = { session_id: 's', model_id: 'A', workspace_root: null, state: 'running', updated_at: '2026-10-17T00:00:00Z', last_run_status: null };
  const batch = { approval_batch_id: 'b', kind: 'live_child' as const, agent_path: record.agent_path, agent: 'tester', origin_run_id: 'r',
    items: [{ approval_id: 'a', tool_call_id: 'inner', tool_name: 'bash', target: 'pytest' }] };
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
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
  expect(within(panel).getByRole('table')).toBeTruthy();
  expect(within(panel).getByRole<HTMLInputElement>('checkbox').disabled).toBe(true);
  expect(within(panel).getByText('旧说明').tagName).toBe('DEL');
  expect(within(panel).queryByText(/tester/)).toBeNull();
  expect(within(panel).queryByRole('radio')).toBeNull();
  const main = screen.getByRole('main');
  fireEvent.click(within(main).getByRole('button', { name: '批准 1' }));
  await waitFor(() => expect(approve).toHaveBeenCalledWith('s', batch, [{ approval_id: 'a', decision: 'approve' }], []));
  expect(api.events).toHaveBeenCalledTimes(1);
});

it.each(['completed', 'stopped'])('renders child deltas and %s in the real sidebar without mixing root messages', async status => {
  const api = new WebApi('test');
  const record = { agent_id: 'child-stream', agent_path: 'root/tester/child-stream', agent: 'tester',
    task: 'inspect', tool_call_id: 'outer', status: 'running' as const };
  const source = { agent_id: record.agent_id, agent_path: record.agent_path,
    agent_run_id: 'child-run', parent_run_id: 'r', parent_tool_call_id: 'outer' };
  const parent = { id: 'parent', type: 'ai', content: '父任务正文', provisional: false, state: 'complete' as const,
    tool_calls: [{ id: 'outer', name: 'subagent', args: { agent: 'tester', task: 'inspect' } }] };
  let run: RunView = { session_id: 's', run_id: 'r', status: 'running', messages: [parent],
    last_sequence: 0, done: false, incomplete: false, manual_review_required: false };
  run = applyEvent(run, { version: 1, session_id: 's', run_id: 'r', sequence: 1, source,
    event_type: 'message_delta', data: { message_id: 'child-answer', index: 1, text: '**子任务检查**\n\n- [x] 已读取\n\n```python\nreturn "child"' } });
  const row = { session_id: 's', model_id: 'A', workspace_root: null, state: 'running',
    updated_at: '2026-10-17T00:00:00Z', last_run_status: null };
  vi.spyOn(api, 'serviceInfo').mockResolvedValue({ workspace_root: null, workspace_label: null });
  vi.spyOn(api, 'models').mockResolvedValue({ default_model_id: 'A', model_ids: ['A'] });
  vi.spyOn(api, 'sessionControlOptions').mockResolvedValue({ tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] });
  vi.spyOn(api, 'sessions').mockResolvedValue([row]);
  vi.spyOn(api, 'session').mockResolvedValue({ ...row, messages: [parent], busy: true, can_run: false,
    active_tools: [], permission_mode: 'Default', has_compaction: false, active_run_id: 'r', latest_run_id: 'r',
    manual_review_required: false, pending_approval: false, approval_batches: [], live: run });
  vi.spyOn(api, 'prepare').mockImplementation(id => api.session(id));
  vi.spyOn(api, 'subagents').mockResolvedValue([record]);
  vi.spyOn(api, 'subagent').mockResolvedValue({ ...record, messages: [], available: true, has_compaction: false, tool_states: {} });
  let receive!: (frame: SseFrame) => void;
  vi.spyOn(api, 'events').mockImplementation((_id, _after, signal, handler) => {
    receive = handler;
    return new Promise(resolve => signal.addEventListener('abort', () => resolve()));
  });
  render(<App api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: /A · 未完成/ }));
  await waitFor(() => expect(receive).toBeTypeOf('function'));
  fireEvent.click(screen.getByRole('button', { name: '查看子智能体 tester · child-' }));
  const panel = await screen.findByRole('complementary', { name: '子智能体' });
  expect((await within(panel).findByText('子任务检查')).tagName).toBe('STRONG');
  expect(within(panel).getByText('正在生成')).toBeTruthy();
  expect(within(panel).getByRole<HTMLInputElement>('checkbox').disabled).toBe(true);
  const code = panel.querySelector('pre code');
  await waitFor(() => expect(code?.querySelector('.hljs-keyword')?.textContent).toBe('return'));
  expect(within(panel).getByRole('button', { name: '复制代码' })).toBeTruthy();
  expect(within(screen.getByRole('main')).queryByRole('button', { name: '复制代码' })).toBeNull();
  expect(within(screen.getByRole('main')).queryByText('子任务检查')).toBeNull();
  if (status === 'completed') {
    await act(async () => receive({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'r', sequence: 2,
      source, event_type: 'message_completed', data: { snapshot_json: JSON.stringify({ id: 'child-answer', type: 'ai',
        content: '**子任务检查完成**\n\n- [x] 已读取\n\n```python\nreturn "child-complete"\n```' }) } } }));
    expect((await within(panel).findByText('子任务检查完成')).tagName).toBe('STRONG');
  } else {
    await act(async () => receive({ event: 'runtime', data: { version: 1, session_id: 's', run_id: 'r', sequence: 2,
      source, event_type: 'run_finished', data: { status: 'stopped' } } }));
    await within(panel).findByText('未完成内容');
    expect(within(panel).getByText('子任务检查').tagName).toBe('STRONG');
  }
  expect(within(panel).queryByText('正在生成')).toBeNull();
  expect(panel.querySelector('pre code')).toBe(code);
  expect(within(panel).getByRole('button', { name: '复制代码' })).toBeTruthy();
  expect(within(screen.getByRole('main')).queryByText('子任务检查完成')).toBeNull();
  expect(api.events).toHaveBeenCalledTimes(1);
});
