import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ToolCall from './ToolCall';
import ApprovalPanel from './ApprovalPanel';
import ComposerControls from './ComposerControls';
import { WebApi } from '../api';
import type { McpResultView } from '../types';

afterEach(cleanup);
const source = { server_id: 'local', remote_tool: 'echo', transport: 'stdio' as const };
const call = { id: 'c', name: 'mcp__local__echo', args: { text: 'hello' } };

it.each(['success', 'tool_error', 'not_executed', 'outcome_unknown', 'unsupported_result'] as const)(
  'shows the MCP %s fact even when older live tool status only says error', outcome => {
    const labels = { success: '成功', tool_error: '工具返回错误', not_executed: '未执行',
      outcome_unknown: '结果未知', unsupported_result: '返回内容暂不支持' };
    const mcp: McpResultView = { version: 1, source, outcome };
    const { container } = render(<ToolCall call={call} initiallyOpen result={{ id: 'm', type: 'tool',
      content: '<remote>literal</remote>', provisional: false, state: 'complete', status: outcome === 'success' ? 'success' : 'error', mcp_result: mcp }}
      phase={{ tool_call_id: 'c', tool_name: call.name, run_id: 'r', state: 'finished', outcome: 'error_result' }} />);
    expect(screen.getAllByText(labels[outcome]).length).toBeGreaterThan(0);
    expect(screen.getByText(/MCP 来源：local \/ echo/)).toBeTruthy();
    expect(container.querySelector('remote')).toBeNull();
    if (outcome === 'outcome_unknown') expect(screen.getByText(/远端可能已产生副作用/)).toBeTruthy();
    expect(screen.queryByText(/归档/)).toBeNull();
  });

it.each([
  { complete: true, prefix: '' }, { complete: false, prefix: '' },
  { complete: true, prefix: '/configured-data/workspaces/project/artifacts/' },
  { complete: false, prefix: 'C:\\configured-data\\sessions\\session\\artifacts\\' },
])('shows complete=$complete archive reference with prefix=$prefix as text', ({ complete, prefix }) => {
  const path = prefix + 'naiyue-agent-output/' + 'a'.repeat(32) + '-raw.log';
  const { container } = render(<ToolCall call={call} initiallyOpen result={{ id: 'm', type: 'tool', content: 'bounded',
    provisional: false, state: 'complete', status: 'success', mcp_result: { version: 1, source, outcome: 'success',
      truncated: true, result_bytes: 9999, archive_complete: complete, archive_paths: { raw: path },
      archive_reason: complete ? undefined : 'command_limit' } }} />);
  expect(screen.getByText(/结果已截断/)).toBeTruthy();
  expect(screen.getByText(complete ? '归档完整' : '归档不完整或不可用')).toBeTruthy();
  expect(screen.getByText(path)).toBeTruthy();
  expect(container.querySelector('a')).toBeNull();
  if (!complete) expect(screen.getByText(/单次归档达到上限/)).toBeTruthy();
});

it('explains the MCP approval source and session scope while keeping the existing decision identity', () => {
  const submit = vi.fn();
  render(<ApprovalPanel disabled={false} submit={submit} batch={{ approval_batch_id: 'b', kind: 'live_child',
    agent_path: 'root/tester/id', agent: 'tester', origin_run_id: 'r', items: [{ approval_id: 'a',
      tool_call_id: 'c', tool_name: call.name, target: '{}', remember_scope: 'tool', mcp_source: source }] }} />);
  expect(screen.getByText(/MCP 来源：local \/ echo/)).toBeTruthy();
  expect(screen.getByText(/根与子任务共用/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '本会话始终允许此工具 1' }));
  expect(submit).toHaveBeenCalledExactlyOnceWith([{ approval_id: 'a', decision: 'approve' }], ['a']);
});

it('shows source and explicit child roles while changing only the main Agent selection', () => {
  const selectTools = vi.fn();
  render(<ComposerControls options={{ tools: [{ name: call.name, description: 'external', mcp_source: source,
    subagent_roles: ['tester'] }], default_active_tools: [], default_permission_mode: 'Default', permission_modes: ['Default'] }}
    tools={[]} disabled={false} loading={false} error={false} reload={vi.fn()} selectMode={vi.fn()} selectTools={selectTools}
    sessionId="one" menu="tools" setMenu={vi.fn()} runCommand={vi.fn()} compactDisabled={false}
    api={new WebApi('fixture')} selectSkill={vi.fn()} focusEditor={vi.fn()} />);
  expect(screen.getByText(/MCP 来源：local \/ echo/)).toBeTruthy();
  expect(screen.getByText('子 Agent 角色：tester')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(selectTools).toHaveBeenCalledExactlyOnceWith([call.name]);
  expect(screen.getByText('下一次模型调用生效。')).toBeTruthy();
});
