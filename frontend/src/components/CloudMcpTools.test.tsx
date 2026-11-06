import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { CloudApi } from '../cloudApi';
import CloudMcpTools from './CloudMcpTools';

afterEach(cleanup);

it('shows the confirmed catalog without tool selection and returns focus on Escape', async () => {
  const mcp = vi.fn().mockResolvedValue({ status: 'available', refresh_id: null, services: [
    { server_id: 'logs', route: 'windows_tunnel', status: 'available', tools: ['mcp__logs__search'] },
  ] });
  const refreshMcp = vi.fn().mockImplementation(async () => {
    const next = { status: 'waiting_run', refresh_id: 'current', services: [] };
    mcp.mockResolvedValue(next);
    return next;
  });
  render(<CloudMcpTools api={{ mcp, refreshMcp } as unknown as CloudApi}
    sid="session" bid="binding" active supported />);
  const trigger = screen.getByRole('button', { name: 'MCP 工具' });
  fireEvent.click(trigger);
  const dialog = await screen.findByRole('dialog', { name: 'MCP 工具' });
  expect(await within(dialog).findByText('mcp__logs__search')).toBeTruthy();
  expect(within(dialog).getByText('Windows 中转')).toBeTruthy();
  expect(within(dialog).queryByRole('checkbox')).toBeNull();
  fireEvent.click(within(dialog).getByRole('button', { name: '刷新工具' }));
  expect(await within(dialog).findByText('本轮结束后刷新')).toBeTruthy();
  expect(refreshMcp).toHaveBeenCalledWith('session', 'binding', expect.any(String));
  fireEvent.keyDown(dialog, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(document.activeElement).toBe(trigger);
});
