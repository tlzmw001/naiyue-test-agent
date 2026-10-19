import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from '../api';
import SubagentSidebar from './SubagentSidebar';
import type { ComponentProps } from 'react';
import { useSubagentList } from '../useSubagentList';

function TestSidebar(props: Omit<ComponentProps<typeof SubagentSidebar>, 'list'>) {
  const list = useSubagentList(props.api, props.sessionId, props.run);
  return <SubagentSidebar {...props} list={list} />;
}

const record = { agent_id: 'abcdef123', agent_path: 'root/tester/abcdef123', agent: 'tester',
  task: 'inspect permissions', tool_call_id: 'outer', status: 'completed' as const };
const detail = { ...record, available: true, has_compaction: false, tool_states: {}, messages: [
  { id: 'seed', type: 'human', content: 'delegated task', provisional: false, state: 'complete' as const },
  { id: 'ai', type: 'ai', content: 'child explanation', provisional: false, state: 'complete' as const,
    tool_calls: [{ id: 'inner', name: 'read', args: { path: 'a' } }] },
  { id: 'tool', type: 'tool', content: 'actual result', tool_call_id: 'inner', status: 'success' as const,
    provisional: false, state: 'complete' as const },
] };
afterEach(cleanup);
function setup() {
  const api = new WebApi('test');
  vi.spyOn(api, 'subagents').mockResolvedValue([record]);
  vi.spyOn(api, 'subagent').mockResolvedValue(detail);
  const close = vi.fn(), showApprovals = vi.fn(), stopAgent = vi.fn();
  return { api, close, showApprovals, stopAgent };
}
it('loads a child preview using its own saved scope and conversation revision', async () => {
  const props = setup();
  const preview = { ...detail.messages[1], id: 'child-preview', content: 'child preview', content_truncated: true,
    sequence: 2, revision: 3, detail: { sequence: 2, revision: 3 } };
  vi.mocked(props.api.subagent).mockResolvedValue({ ...detail, messages: [preview] });
  const load = vi.spyOn(props.api, 'messageContent').mockResolvedValue('complete saved child body');
  render(<TestSidebar {...props} sessionId="s" conversationRevision={4} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' }));
  expect(await screen.findByText('complete saved child body')).toBeTruthy();
  expect(load).toHaveBeenCalledWith('s', expect.objectContaining({ id: 'child-preview', detail: preview.detail }),
    4, record.agent_id, expect.any(AbortSignal));
});
it('selects by name then reuses the conversation stream without a detail name or approval controls', async () => {
  const props = setup();
  const { container } = render(<TestSidebar {...props} sessionId="s" />);
  fireEvent.click(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' }));
  await screen.findByText('child explanation');
  expect(screen.getByText('delegated task')).toBeTruthy();
  expect(screen.getByText('read')).toBeTruthy();
  expect(screen.getAllByText('actual result')).toHaveLength(1);
  expect(screen.queryByText(/tester/)).toBeNull();
  expect(container.querySelector('hr')).toBeTruthy();
  expect(screen.queryByRole('radio')).toBeNull();
  expect(screen.queryByRole('textbox')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '返回名称列表' }));
  expect(screen.getByRole('button', { name: '查看子智能体 tester · abcdef' })).toBeTruthy();
});
it('keeps live messages and progress isolated and sends approvals back to the main conversation', async () => {
  const props = setup();
  const source = { agent_path: record.agent_path, agent_id: record.agent_id, agent_run_id: 'child',
    parent_run_id: 'root-run', parent_tool_call_id: 'outer' };
  const run = { run_id: 'root-run', session_id: 's', status: 'running', done: false, messages: [], last_sequence: 1,
    incomplete: false, manual_review_required: false, agents: { [record.agent_path]: {
      source, status: 'awaiting_approval', messages: detail.messages, tool_states: { inner: {
        tool_call_id: 'inner', tool_name: 'read', run_id: 'child', batch_id: 'ai', state: 'running',
        phase: 'read', activity: 'running', text: 'current progress' } },
    } } } as any;
  render(<TestSidebar {...props} sessionId="s" run={run} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' }));
  await screen.findByText('current progress');
  fireEvent.click(screen.getByRole('button', { name: '到主会话处理' }));
  expect(props.showApprovals).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: '停止该子智能体' }));
  expect(props.stopAgent).toHaveBeenCalledWith(record.agent_path, 'root-run');
  expect(within(screen.getByRole('complementary')).queryByRole('radio')).toBeNull();
});
it('discards a late detail response after changing session and offers retry on read failure', async () => {
  const props = setup();
  let resolve!: (value: typeof detail) => void;
  vi.mocked(props.api.subagent).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const { rerender } = render(<TestSidebar {...props} sessionId="s" />);
  fireEvent.click(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' }));
  await waitFor(() => expect(props.api.subagent).toHaveBeenCalledOnce());
  vi.mocked(props.api.subagents).mockRejectedValueOnce(new Error('read failed'));
  rerender(<TestSidebar {...props} sessionId="other" />);
  await screen.findByRole('button', { name: '重试' });
  await act(async () => resolve(detail));
  expect(screen.queryByText('child explanation')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' })).toBeTruthy();
});
it('keeps the second instance selected when the first detail returns late', async () => {
  const props = setup();
  const second = { ...record, agent_id: 'second123', agent_path: 'root/tester/second123', task: 'second task' };
  vi.mocked(props.api.subagents).mockResolvedValue([record, second]);
  let resolveFirst!: (value: typeof detail) => void;
  vi.mocked(props.api.subagent).mockImplementationOnce(() => new Promise(done => { resolveFirst = done; }))
    .mockResolvedValueOnce({ ...detail, ...second, messages: [{ ...detail.messages[0], content: 'SECOND_INSTANCE_ONLY' }] });
  render(<TestSidebar {...props} sessionId="s" />);
  fireEvent.click(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' }));
  await waitFor(() => expect(props.api.subagent).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: '返回名称列表' }));
  fireEvent.click(screen.getByRole('button', { name: '查看子智能体 tester · second' }));
  await screen.findByText('SECOND_INSTANCE_ONLY');
  await act(async () => resolveFirst(detail));
  expect(screen.getByText('SECOND_INSTANCE_ONLY')).toBeTruthy();
  expect(screen.queryByText('child explanation')).toBeNull();
});
it('keeps detail progress text when a compact root snapshot contains only phase metadata', async () => {
  const props = setup();
  vi.mocked(props.api.subagent).mockResolvedValue({ ...detail, tool_states: { inner: {
    tool_call_id: 'inner', tool_name: 'read', run_id: 'child', batch_id: 'ai', state: 'running', text: 'DETAIL_PROGRESS_TAIL' } } });
  const source = { agent_path: record.agent_path, agent_id: record.agent_id, agent_run_id: 'child', parent_run_id: 'r', parent_tool_call_id: 'outer' };
  const run = { session_id: 's', run_id: 'r', status: 'running', messages: [], done: false, last_sequence: 10,
    incomplete: false, manual_review_required: false, agents: { [record.agent_path]: {
      source, status: 'running', messages: [], tool_states: { inner: { tool_call_id: 'inner', tool_name: 'read',
        run_id: 'child', batch_id: 'ai', state: 'running', activity: 'running', phase: 'read' } } } } } as any;
  render(<TestSidebar {...props} sessionId="s" run={run} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' }));
  expect(await screen.findByText('DETAIL_PROGRESS_TAIL')).toBeTruthy();
});
it('refreshes the selected detail after a compact snapshot replaces its live history', async () => {
  const props = setup();
  const source = { agent_path: record.agent_path, agent_id: record.agent_id, agent_run_id: 'child', parent_run_id: 'r', parent_tool_call_id: 'outer' };
  const run = { session_id: 's', run_id: 'r', status: 'running', messages: [], done: false, last_sequence: 10,
    incomplete: false, manual_review_required: false, agents: { [record.agent_path]: {
      source, status: 'running', snapshot_sequence: 10, messages: [], tool_states: {} } } } as any;
  const { rerender } = render(<TestSidebar {...props} sessionId="s" run={run} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看子智能体 tester · abcdef' }));
  await screen.findByText('child explanation');
  vi.mocked(props.api.subagent).mockResolvedValue({ ...detail, messages: [...detail.messages,
    { id: 'last', type: 'ai', content: 'FRESH_SNAPSHOT_REPORT', provisional: false, state: 'complete' }] });
  rerender(<TestSidebar {...props} sessionId="s" run={{ ...run, last_sequence: 20, agents: {
    [record.agent_path]: { ...run.agents[record.agent_path], snapshot_sequence: 20 } } }} />);
  expect(await screen.findByText('FRESH_SNAPSHOT_REPORT')).toBeTruthy();
});
