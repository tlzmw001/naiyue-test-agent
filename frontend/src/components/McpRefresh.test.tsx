import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from '../api';
import ComposerControls from './ComposerControls';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function setup(extra: Partial<ComponentProps<typeof ComposerControls>> = {}) {
  const api = new WebApi('fixture');
  const reload = vi.fn();
  const refresh = vi.spyOn(api, 'refreshMcp').mockResolvedValue({ servers: [{ server_id: 'local', status: 'enabled',
    enabled: true, selected_tools: ['echo'] }] });
  render(<ComposerControls options={{ tools: [], permission_modes: ['Default'], default_permission_mode: 'Default', default_active_tools: [] }}
    tools={[]} disabled={false} loading={false} error={false} reload={reload} selectMode={vi.fn()} selectTools={vi.fn()}
    sessionId={null} menu="tools" setMenu={vi.fn()} runCommand={vi.fn()} compactDisabled={false} api={api}
    selectSkill={vi.fn()} focusEditor={vi.fn()} {...extra} />);
  return { api, refresh, reload };
}

it('refreshes MCP configuration without sending a message or creating a session', async () => {
  const { refresh, reload } = setup();
  expect(screen.getByText('下一次模型调用生效。')).toBeTruthy();
  expect(screen.queryByText(/不修改子 Agent 的角色工具集合/)).toBeNull();
  expect(screen.queryByText(/保存 settings.toml 后刷新/)).toBeNull();
  expect(screen.queryByRole('button', { name: '刷新 MCP 配置' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  await screen.findByText(/local：已启用/);
  expect(screen.queryByText('MCP 配置刷新完成')).toBeNull();
  expect(refresh).toHaveBeenCalledOnce();
  expect(reload).toHaveBeenCalledOnce();
  expect(screen.getByText(/local：已启用/)).toBeTruthy();
});

it('shows per-server failure and retains the effective enabled state', async () => {
  const { refresh } = setup();
  refresh.mockResolvedValueOnce({ servers: [{ server_id: 'local', status: 'error', enabled: true,
    selected_tools: ['echo'], error: 'mcp_refresh_failed' }] });
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  await screen.findByText(/local：刷新失败，原服务仍启用/);
});

it('shows a refresh failure without pretending the existing tool list changed', async () => {
  const { refresh, reload } = setup();
  refresh.mockRejectedValueOnce(new Error('PRIVATE'));
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  await screen.findByText(/MCP 配置刷新失败/);
  expect(reload).toHaveBeenCalledOnce();
  expect(screen.queryByText('PRIVATE')).toBeNull();
});

it('blocks duplicate refresh submissions until the first result arrives', async () => {
  const { refresh, reload } = setup();
  let finish!: (value: { servers: [] }) => void;
  refresh.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  const button = screen.getByRole('button', { name: '正在刷新…' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.click(button);
  expect(reload).not.toHaveBeenCalled();
  finish({ servers: [] });
  await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(refresh).toHaveBeenCalledOnce();
});

it('retains saved selection but disables unavailable tools and explains the service state', () => {
  setup({ tools: ['mcp__local__echo'], options: { default_active_tools: [], default_permission_mode: 'Default', permission_modes: ['Default'],
    tools: [{ name: 'mcp__local__echo', description: 'external', available: false, unavailable_reason: 'server_disabled',
      mcp_source: { server_id: 'local', remote_tool: 'echo' } }],
    mcp_servers: [{ server_id: 'local', status: 'disabled', enabled: false, selected_tools: [] }] } });
  const check = screen.getByRole('checkbox') as HTMLInputElement;
  expect(check.checked).toBe(true); expect(check.disabled).toBe(true);
  expect(screen.getByText(/服务已停用/)).toBeTruthy();
});

it('keeps permission-mode refresh read-only', () => {
  const { refresh, reload } = setup({ menu: 'modes' });
  fireEvent.click(screen.getByRole('button', { name: '刷新工具与模式' }));
  expect(reload).toHaveBeenCalledOnce();
  expect(refresh).not.toHaveBeenCalled();
});
