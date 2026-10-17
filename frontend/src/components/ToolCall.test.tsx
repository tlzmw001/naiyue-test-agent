import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Conversation from './Conversation';
import ToolCall from './ToolCall';

afterEach(cleanup);
it('stops the exact child scope and disables repeated stop during cleanup', () => {
  const stop = vi.fn();
  const agent = { source: { agent_path: 'root/tester/A', agent_id: 'A', agent_run_id: 'segment',
    parent_run_id: 'root-run', parent_tool_call_id: 'outer' }, status: 'running' as const, tool_states: {} };
  const call = { id: 'outer', name: 'subagent', args: {} };
  const { rerender } = render(<ToolCall call={call} agents={[agent]} stopAgent={stop} />);
  fireEvent.click(screen.getByRole('button', { name: '停止该子任务' }));
  expect(stop).toHaveBeenCalledWith('root/tester/A', 'root-run');
  rerender(<ToolCall call={call} agents={[{ ...agent, status: 'stopping' }]} stopAgent={stop} />);
  expect((screen.getByRole('button', { name: '正在停止子任务…' }) as HTMLButtonElement).disabled).toBe(true);
});
it('shows a waiting child on its parent delegation summary', () => {
  render(<ToolCall call={{ id: 'outer', name: 'subagent', args: { agent: 'tester', task: 'verify' } }}
    phase={{ tool_call_id: 'outer', tool_name: 'subagent', run_id: 'root-run', state: 'running', phase: 'authorize' }}
    agents={[{ source: { agent_path: 'root/tester/A', agent_id: 'A', agent_run_id: 'child-run',
      parent_run_id: 'root-run', parent_tool_call_id: 'outer' }, status: 'awaiting_approval', tool_states: {} }]} />);
  expect(screen.getByText('等待子任务审批')).toBeTruthy();
  expect(screen.queryByText('准备执行')).toBeNull();
});

it('renders a correlated tool result once, escapes content, and separates it from assistant speech', () => {
  const { container } = render(<Conversation compacted={false} messages={[
    { id: 'ai', type: 'ai', content: '', state: 'complete', provisional: false,
      tool_calls: [{ id: 't', name: 'read', args: { path: 'a.txt' } }] },
    { id: 'result', type: 'tool', content: '<script>tool body</script>', tool_call_id: 't', status: 'error',
      state: 'complete', provisional: false },
  ] as any} />);
  expect(screen.getByText('read')).toBeTruthy();
  expect(screen.getByText('错误结果')).toBeTruthy();
  expect(screen.getAllByText('<script>tool body</script>')).toHaveLength(1);
  expect(container.querySelector('script')).toBeNull();
});

it('shows real progress with an advancing local clock, escaped latest tail, and terminal status', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-16T00:00:10Z'));
  try {
    const call = { id: 't', name: 'bash', args: { command: 'pytest' } };
    const phase = { tool_call_id: 't', tool_name: 'bash', run_id: 'r', batch_id: 'ai',
      state: 'running', boundary: 'dispatch', activity: 'running', phase: 'process_output',
      completed: 8, total: null, unit: 'bytes', text: '<img src=x onerror=alert(1)>',
      started_at: '2026-10-16T00:00:00Z', last_activity_at: '2026-10-16T00:00:08Z' } as any;
    const { container, rerender } = render(<ToolCall call={call} phase={phase} />);
    expect(screen.getByText('输出更新')).toBeTruthy();
    expect(screen.getByText('已历时 10秒')).toBeTruthy();
    expect(screen.getByText('距最后活动 2秒')).toBeTruthy();
    expect(screen.getByText('8 bytes')).toBeTruthy();
    expect(screen.queryByText(/%/)).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByText('距最后活动 4秒')).toBeTruthy();
    rerender(<ToolCall call={call} phase={phase} stopping />);
    expect(screen.getByText('停止中')).toBeTruthy();
    rerender(<ToolCall call={call} phase={{ ...phase, state: 'finished', outcome: 'success',
      finished_at: '2026-10-16T00:00:12Z' }} stopping />);
    expect(screen.getByText('成功')).toBeTruthy();
    expect(screen.queryByText('停止中')).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  } finally { vi.useRealTimers(); }
});

it('binds progress only to its AI batch when a call ID is reused in the same run', () => {
  const messages = ['old', 'current'].map(id => ({ id, type: 'ai', content: '', run_id: 'r',
    state: 'complete', provisional: false, tool_calls: [{ id: 't', name: 'read', args: { path: id } }] }));
  render(<Conversation compacted={false} messages={messages as any} toolStates={{ t: {
    tool_call_id: 't', tool_name: 'read', run_id: 'r', batch_id: 'current', state: 'running',
    boundary: 'dispatch', activity: 'queued', phase: 'batch_wait', text: 'CURRENT_PROGRESS',
  } as any }} />);
  expect(screen.getAllByText('排队中')).toHaveLength(1);
  expect(screen.getAllByText('CURRENT_PROGRESS')).toHaveLength(1);
});

it('does not attach a later tool result to an earlier turn when a call ID is reused', () => {
  render(<Conversation compacted={false} messages={[
    { id: 'ai-one', type: 'ai', content: '', state: 'complete', provisional: false,
      tool_calls: [{ id: 'reused', name: 'read', args: { path: 'first.txt' } }] },
    { id: 'one', type: 'tool', content: 'first result', tool_call_id: 'reused', status: 'success', state: 'complete', provisional: false },
    { id: 'ai-two', type: 'ai', content: '', state: 'complete', provisional: false,
      tool_calls: [{ id: 'reused', name: 'read', args: { path: 'second.txt' } }] },
    { id: 'two', type: 'tool', content: 'second result', tool_call_id: 'reused', status: 'error', state: 'complete', provisional: false },
  ] as any} />);
  expect(screen.getAllByText('first result')).toHaveLength(1);
  expect(screen.getAllByText('second result')).toHaveLength(1);
});

it('binds delegated instances to their parent AI batch when outer call IDs repeat', () => {
  const open = vi.fn();
  const agents = Object.fromEntries(['one', 'two'].map(batch => [batch, {
    source: { agent_path: `root/tester/${batch}`, agent_id: batch, agent_run_id: batch,
      parent_run_id: 'r', parent_tool_call_id: 'same' }, parent_batch_id: batch,
    status: 'completed', tool_states: {}, messages: [],
  }]));
  const { container } = render(<Conversation compacted={false} openAgent={open} agents={agents as any} messages={[
    { id: 'one', type: 'ai', content: 'first batch', run_id: 'r', state: 'complete', provisional: false,
      tool_calls: [{ id: 'same', name: 'subagent', args: { agent: 'tester', task: 'first task' } }] },
    { id: 'result', type: 'tool', content: 'first report', tool_call_id: 'same', state: 'complete', provisional: false },
    { id: 'two', type: 'ai', content: 'second batch', run_id: 'r', state: 'complete', provisional: false,
      tool_calls: [{ id: 'same', name: 'subagent', args: { agent: 'tester', task: 'second task' } }] },
  ] as any} />);
  const rows = container.querySelectorAll('article');
  expect(rows[0].textContent).toContain('tester · one');
  expect(rows[0].textContent).not.toContain('tester · two');
  expect(rows[1].textContent).toContain('tester · two');
  expect(rows[1].textContent).not.toContain('tester · one');
});
