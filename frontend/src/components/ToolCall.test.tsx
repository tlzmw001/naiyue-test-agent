import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ToolState } from '../types';
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
  expect(stop).toHaveBeenCalledWith('root/tester/A', 'root-run', 'segment');
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

it('collapses a completed tool to its identity, target and outcome, retaining progress in details', () => {
  const { container } = render(<ToolCall call={{ id: 'ls', name: 'ls', args: { path: '.', limit: 100 } }}
    phase={{ tool_call_id: 'ls', tool_name: 'ls', run_id: 'r', state: 'finished', outcome: 'success',
      phase: 'list_results', completed: 8, unit: 'entries', text: 'README.md',
      started_at: '2026-10-16T00:00:00Z', finished_at: '2026-10-16T00:00:12Z' }}
    result={{ id: 'result', type: 'tool', content: 'README.md\nfrontend/', state: 'complete', provisional: false }} />);
  const card = container.querySelector('details')!;
  const summary = card.querySelector('summary')!;
  expect(card.open).toBe(false);
  expect(summary.textContent).toContain('ls');
  expect(summary.querySelector('code')?.textContent).toBe('.');
  expect(summary.textContent).toContain('成功');
  expect(summary.textContent).not.toContain('整理目录结果');
  expect(summary.textContent).not.toContain('已历时');
  fireEvent.click(summary);
  expect(card.open).toBe(true);
  expect(screen.getByText('整理目录结果').closest('summary')).toBeNull();
  expect(screen.getByText('已历时 12秒').closest('summary')).toBeNull();
  expect(screen.getByText('8 entries')).toBeTruthy();
  expect(container.querySelectorAll('pre')[2].textContent).toBe('README.md\nfrontend/');
  expect(container.querySelector('pre')?.textContent).toBe('README.md');
});

it.each([
  ['bash', { command: 'pytest -q' }, 'pytest -q'],
  ['read', { path: 'src/main.py', offset: 5 }, 'src/main.py'],
  ['write', { path: 'report.md', content: 'DO_NOT_PUT_BODY_IN_SUMMARY' }, 'report.md'],
  ['edit', { path: 'src/app.py', edits: [{ oldText: 'before', newText: 'after' }] }, 'src/app.py'],
  ['grep', { pattern: 'TODO', path: 'src' }, 'TODO · src'],
  ['find', { pattern: '*.py', path: '.' }, '*.py · .'],
  ['subagent', { agent: 'tester', task: 'DO_NOT_PUT_TASK_IN_SUMMARY' }, 'tester'],
])('shows bounded relevant arguments for %s without putting full payloads in the summary', (name, args, target) => {
  const { container } = render(<ToolCall call={{ id: 't', name, args }} />);
  const summary = container.querySelector('summary')!;
  expect(summary.querySelector('code')?.textContent).toBe(target);
  expect(summary.textContent).not.toContain('DO_NOT_PUT');
  expect(JSON.parse(container.querySelector('pre')!.textContent!)).toEqual(args);
});

it.each([
  ['read', {}], ['ls', {}], ['bash', { command: 42 }], ['custom', { content: 'private body', path: 'unrecognized' }],
])('does not invent missing targets or infer arguments of an unknown tool: %s %j', (name, args) => {
  const { container } = render(<ToolCall call={{ id: 't', name, args }} />);
  const summary = container.querySelector('summary')!;
  expect(summary.querySelector('code')).toBeNull();
  expect(summary.textContent).toContain(name);
  expect(summary.textContent).not.toContain('private body');
  expect(summary.textContent).toContain('待执行');
});

it('bounds long multiline summaries and preserves full original arguments and escaped content', () => {
  const command = '<img src=x onerror=alert(1)>\n' + '长命令参数/'.repeat(80);
  const { container } = render(<ToolCall call={{ id: 't', name: 'bash', args: { command } }} />);
  const summary = container.querySelector('summary')!;
  const target = summary.querySelector('code')!;
  expect(target.textContent!.length).toBeLessThanOrEqual(160);
  expect(target.textContent).toMatch(/…$/);
  expect(target.textContent).not.toContain('\n');
  expect(JSON.parse(container.querySelector('pre')!.textContent!).command).toBe(command);
  expect(container.querySelector('img')).toBeNull();
});

it.each([
  ['error_result', '错误结果'], ['not_executed', '未执行'], ['exception', '执行异常'],
  ['outcome_unknown', '结果待核对'], ['new_unrecognized_outcome', '状态未确认'],
])('keeps terminal failure or uncertainty visible in the collapsed summary: %s', (outcome, label) => {
  const { container } = render(<ToolCall call={{ id: 't', name: 'read', args: { path: 'missing.txt' } }}
    phase={{ tool_call_id: 't', tool_name: 'read', run_id: 'r', state: 'finished', outcome,
      phase: 'read', reason: 'original_reason', started_at: '2026-10-16T00:00:00Z', finished_at: '2026-10-16T00:00:12Z' }} />);
  const summary = container.querySelector('summary')!;
  expect(summary.querySelector('code')?.textContent).toBe('missing.txt');
  expect(summary.textContent).toContain(label);
  expect(summary.textContent).not.toContain('成功');
  expect(summary.textContent).not.toContain('读取文件');
  expect(summary.textContent).not.toContain('已历时');
  expect(screen.getByText('原因：original_reason')).toBeTruthy();
});

it('does not promote a result without a confirmed outcome, and preserves unconfirmed precedence', () => {
  const call = { id: 't', name: 'read', args: { path: 'a.txt' } };
  const result = { id: 'result', type: 'tool', content: 'original result', state: 'complete' as const, provisional: false };
  const phase: ToolState = { tool_call_id: 't', tool_name: 'read', run_id: 'r', state: 'finished', outcome: 'success' };
  const { container, rerender } = render(<ToolCall call={call} result={result} />);
  expect(container.querySelector('summary')!.textContent).toContain('已有结果');
  expect(container.querySelector('summary')!.textContent).not.toContain('成功');
  expect(container.querySelector('summary span[aria-hidden="true"]')!.textContent).toBe('◇');
  rerender(<ToolCall call={call} phase={phase} result={result} unconfirmed />);
  expect(container.querySelector('summary')!.textContent).toContain('结果未确认');
  expect(container.querySelector('summary')!.textContent).not.toContain('成功');
  rerender(<ToolCall call={call} phase={{ ...phase, state: 'unconfirmed' }} result={result} />);
  expect(container.querySelector('summary')!.textContent).toContain('状态未确认');
});
