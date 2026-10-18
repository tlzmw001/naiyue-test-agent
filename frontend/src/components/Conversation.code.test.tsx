import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { MessageRow } from '../types';
import Conversation from './Conversation';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const message = (content: string, provisional = false): MessageRow => ({
  id: 'code-message', type: 'ai', content, provisional, state: provisional ? 'draft' : 'complete',
});
const view = (content: string, provisional = false) =>
  <Conversation compacted={false} messages={[message(content, provisional)]} />;

it.each([
  ['python', 'def check():\n    return "测试"'], ['js', 'const value = "测试";'],
  ['json', '{"ok": true}'], ['bash', 'echo "测试"'],
])('labels and highlights %s while keeping the code text', (language, code) => {
  const { container } = render(view(`\`\`\`${language}\n${code}\n\`\`\``));
  expect(screen.getByText(language, { selector: 'span' })).toBeTruthy();
  expect(container.querySelector('pre code')?.textContent).toBe(code + '\n');
  expect(container.querySelector('pre code span[class^="hljs-"]')).not.toBeNull();
  expect(screen.getByRole('button', { name: '复制代码' })).toBeTruthy();
});

it.each(['', 'unregistered-tool-language', 'x'.repeat(180)])('keeps unmarked or unknown language %s as plain code', language => {
  const code = '<script>alert("literal")</script>\n  中文  ';
  const { container } = render(view(`\`\`\`${language}\n${code}\n\`\`\``));
  const label = screen.getByText(language || 'text', { selector: 'span' });
  expect(label.getAttribute('title')).toBe(language || 'text');
  expect(container.querySelector('pre code')?.textContent).toBe(code + '\n');
  expect(container.querySelector('pre code span')).toBeNull();
  expect(container.querySelector('script')).toBeNull();
  expect(screen.getByRole('button', { name: '复制代码' })).toBeTruthy();
});

it('leaves inline code without a block toolbar or highlighting', () => {
  const { container } = render(view('执行 `print("hello")` 即可。'));
  expect(container.querySelector('code')?.textContent).toBe('print("hello")');
  expect(container.querySelector('pre')).toBeNull();
  expect(screen.queryByRole('button', { name: '复制代码' })).toBeNull();
});

it('copies only the selected code including whitespace and isolates block feedback from answer copy', async () => {
  const user = userEvent.setup();
  const code = 'def check():\n\treturn "中文"  \n\n';
  const source = `\`\`\`python\n${code}\`\`\`\n\n\`\`\`json\n{"ok": true}\n\`\`\``;
  const { container } = render(view(source));
  const buttons = screen.getAllByRole('button', { name: '复制代码' });
  await user.click(buttons[0]);
  expect(await navigator.clipboard.readText()).toBe(code);
  expect(buttons[0].textContent).toBe('已复制');
  expect(buttons[1].textContent).toBe('复制代码');
  expect(container.querySelectorAll('pre code')).toHaveLength(2);
  await user.click(buttons[1]);
  expect(await navigator.clipboard.readText()).toBe('{"ok": true}\n');
  await user.click(screen.getByRole('button', { name: '复制回答' }));
  expect(await navigator.clipboard.readText()).toBe(source);
});

it('shows clipboard failure and allows retry without changing the code', async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValueOnce(new Error('clipboard unavailable'));
  const { container } = render(view('```python\nprint("hello")\n```'));
  const button = screen.getByRole('button', { name: '复制代码' });
  await user.click(button);
  expect(button.textContent).toBe('复制失败，重试');
  expect(container.querySelector('pre code')?.textContent).toBe('print("hello")\n');
  write.mockResolvedValueOnce();
  await user.click(button);
  expect(button.textContent).toBe('已复制');
  expect(write).toHaveBeenLastCalledWith('print("hello")\n');
});

it('copies received streaming code, clears feedback on update and preserves code DOM and scroll focus', async () => {
  const user = userEvent.setup();
  const { container, rerender } = render(view('```python\ndef check():', true));
  const code = container.querySelector('pre code');
  const scroll = screen.getByRole('region', { name: '代码，可横向滚动' });
  const button = screen.getByRole('button', { name: '复制代码' });
  await user.click(button);
  expect(await navigator.clipboard.readText()).toBe('def check():\n');
  expect(button.textContent).toBe('已复制');
  scroll.focus(); scroll.scrollLeft = 120;
  rerender(view('```python\ndef check():\n    return "下一段"', true));
  expect(button.textContent).toBe('复制代码');
  expect(screen.getByText('正在生成')).toBeTruthy();
  expect(container.querySelector('pre code')).toBe(code);
  expect(document.activeElement).toBe(scroll);
  expect(scroll.scrollLeft).toBe(120);
  rerender(view('```python\ndef check():\n    return "最终校准"\n```'));
  expect(container.querySelector('pre code')).toBe(code);
  expect(screen.queryByText('正在生成')).toBeNull();
  await user.click(button);
  expect(await navigator.clipboard.readText()).toBe('def check():\n    return "最终校准"\n');
});

it.each(['resolve', 'reject'] as const)('ignores a stale clipboard %s after streaming changes the code', async outcome => {
  userEvent.setup();
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  vi.spyOn(navigator.clipboard, 'writeText').mockImplementationOnce(() => new Promise<void>((yes, no) => { resolve = yes; reject = no; }));
  const { rerender } = render(view('```js\nconst oldValue = 1;', true));
  const button = screen.getByRole('button', { name: '复制代码' });
  fireEvent.click(button);
  rerender(view('```js\nconst newValue = 2;', true));
  await act(async () => { if (outcome === 'resolve') resolve(); else reject(new Error('old failure')); });
  expect(button.textContent).toBe('复制代码');
});

it('keeps stopped code highlighted and copyable with the unfinished marker', async () => {
  const user = userEvent.setup();
  const row = { ...message('```python\nreturn "partial"', true), state: 'unconfirmed' as const };
  const { container } = render(<Conversation compacted={false} messages={[row]} />);
  expect(screen.getByText('未完成内容')).toBeTruthy();
  expect(container.querySelector('.hljs-keyword')?.textContent).toBe('return');
  await user.click(screen.getByRole('button', { name: '复制代码' }));
  expect(await navigator.clipboard.readText()).toBe('return "partial"\n');
});

it('keeps highlighted code intact through 32 bounded updates without adding source text', () => {
  const { container, rerender } = render(view('```python\n', true));
  let code = '';
  for (let i = 0; i < 32; i++) {
    code += `value_${i} = "${'测试abc'.repeat(27)}"\n`;
    rerender(view('```python\n' + code, true));
    expect(container.querySelector('pre code')?.textContent).toBe(code);
  }
  expect(new TextEncoder().encode(code).byteLength).toBeGreaterThanOrEqual(8192);
  const region = screen.getByRole('region', { name: '代码，可横向滚动' });
  expect(within(region).queryByRole('button')).toBeNull();
  expect(container.querySelectorAll('.hljs-string')).toHaveLength(32);
  expect(screen.getAllByRole('button', { name: '复制代码' })).toHaveLength(1);
});
