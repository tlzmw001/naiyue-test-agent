import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import ToolCall from './ToolCall';

afterEach(cleanup);
const content = 'edit replaced 1 block(s); 18 bytes\nfirstChangedLine=2\n@@ -1,3 +1,3 @@\n context\n-old value\n+new <tag>\n';
const call = { id: 'c', name: 'edit', args: { path: 'a.txt' } };
const result = { id: 'result', type: 'tool', content, provisional: false, state: 'complete' as const, status: 'success' as const };

it('shows the existing Edit diff and first changed line while preserving the full original result', async () => {
  const { container } = render(<ToolCall call={call} result={result} />);
  fireEvent.click(screen.getByText('edit', { exact: true }));
  expect(screen.getByText('首个修改行：2')).toBeTruthy();
  const diff = screen.getByRole('region', { name: '修改差异' });
  expect(diff.textContent).toBe('@@ -1,3 +1,3 @@\n context\n-old value\n+new <tag>\n');
  expect(diff.querySelector('[data-change="added"]')?.textContent).toBe('+new <tag>\n');
  expect(diff.querySelector('[data-change="removed"]')?.textContent).toBe('-old value\n');
  expect(container.querySelector('tag')).toBeNull();
  fireEvent.click(screen.getByText('原始工具结果'));
  expect(screen.getByLabelText('原始工具结果').textContent).toBe(content);
  await waitFor(() => expect(container.querySelector('[data-message-id="result"]')).toBeTruthy());
});

it('keeps the actual Edit truncation notice and history preview notice visible', () => {
  render(<ToolCall call={call} result={{ ...result, content_truncated: true,
    content: content + '[edit: diff incomplete or truncated by feedback budget]' }} />);
  fireEvent.click(screen.getByText('edit', { exact: true }));
  expect(screen.getByText(/差异不完整或已截断/)).toBeTruthy();
  expect(screen.getByText('当前为工具结果预览。')).toBeTruthy();
});

it.each(['write', 'custom', 'error'])('keeps %s results literal rather than inventing a diff', name => {
  const { container } = render(<ToolCall call={{ ...call, name: name === 'error' ? 'edit' : name }}
    result={{ ...result, status: name === 'error' ? 'error' : 'success' }} />);
  expect(screen.queryByRole('region', { name: '修改差异' })).toBeNull();
  expect(Array.from(container.querySelectorAll('pre')).some(pre => pre.textContent === content)).toBe(true);
});
