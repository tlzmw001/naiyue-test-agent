import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ToolResult from './ToolResult';

afterEach(cleanup);
it('shows a structured write diff and lazily expands its saved preview', async () => {
  const change = { type: 'file_change', version: 1, operation: 'write', path: 'a', firstChangedLine: 1,
    diff: '-old\n+new\n', preview: 'new\n', patch_omitted: false, diff_truncated: false };
  const load = vi.fn(async () => change);
  render(<ToolResult name="write" result={{ id: 't', type: 'tool', content: 'write replaced 4 bytes',
    provisional: false, state: 'complete', sequence: 3, revision: 1, detail: { sequence: 3, revision: 1 }, file_change: change as any }} loadChange={load as any} />);
  expect(screen.getByRole('region', { name: '修改差异' }).textContent).toContain('+new');
  fireEvent.click(screen.getByRole('button', { name: '查看保存的详情' }));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  expect(screen.getByText('保存的修改后预览')).toBeTruthy();
  expect(screen.getByRole('button', { name: '下载 patch' })).toBeTruthy();
});
