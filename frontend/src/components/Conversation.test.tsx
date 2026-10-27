import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import Conversation from './Conversation';

afterEach(cleanup);
it.each([
  '<endpoints>\n  <endpoint name="synthetic" />\n</endpoints>',
  '<!-- synthetic configuration -->',
  '<div>用户粘贴的内容</div>',
  '<script>alert("synthetic")</script>\n<img src="https://example.invalid/image" onerror="alert(1)">',
])('shows pasted user markup as text instead of an empty bubble: %s', content => {
  const { container } = render(<Conversation compacted={false} messages={[{
    id: 'user-markup', type: 'human', content, provisional: false, state: 'complete',
  }]} />);
  const bubble = container.querySelector('article');
  expect(bubble?.textContent).toBe(content);
  expect(bubble?.querySelector('endpoints, endpoint, div > div > div, script, img')).toBeNull();
  expect(bubble?.querySelector('[onerror]')).toBeNull();
});

it('explains why a saved MCP approval requires a new call and only offers cancellation', () => {
  render(<Conversation compacted={false} messages={[]} recovery={{ kind: 'pending_approval',
    reason: 'mcp_approval_requires_new_call', actions: ['continue'], unconfirmed_tool_call_ids: ['old'] }}
    onRecover={() => {}} />);
  expect(screen.getByText('之前的 MCP 调用需要按当前工具定义重新审批。请先取消旧批次，再发起新调用。')).toBeTruthy();
  expect(screen.queryByText('mcp_approval_requires_new_call', { exact: false })).toBeNull();
  expect(screen.queryByRole('button', { name: '重试恢复待审批' })).toBeNull();
  expect(screen.getByRole('button', { name: '结束旧运行并继续会话' })).toBeTruthy();
});
it('renders model Markdown without raw HTML, executable links or remote image requests', () => {
  const { container } = render(<Conversation compacted={false} messages={[{
    id: 'm', type: 'ai', provisional: false, state: 'complete',
    content: '<script>alert(1)</script>\n\n[x](javascript:alert%281%29)\n\n![remote](https://example.com/private.png)\n\n[docs](https://example.com/docs)',
  }]} />);
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('img')).toBeNull();
  expect([...container.querySelectorAll('a')].some(link => link.getAttribute('href')?.startsWith('javascript:'))).toBe(false);
  expect(container.querySelector('a[href="https://example.com/docs"]')?.getAttribute('rel')).toContain('noopener');
});

it('renders aligned GFM tables in a keyboard-accessible local scrolling region', () => {
  render(<Conversation compacted={false} messages={[{
    id: 'table', type: 'ai', provisional: false, state: 'complete',
    content: '| 检查项 | 结果 |\n| :--- | ---: |\n| `pytest` | [通过](https://example.com/result) |',
  }]} />);
  const table = screen.getByRole('table');
  expect(screen.getAllByRole('columnheader').map(node => node.textContent)).toEqual(['检查项', '结果']);
  expect(table.querySelector('th')?.style.textAlign).toBe('left');
  expect(table.querySelectorAll('th')[1].style.textAlign).toBe('right');
  expect(table.querySelector('code')?.textContent).toBe('pytest');
  expect(screen.getByRole('link', { name: '通过' }).getAttribute('href')).toBe('https://example.com/result');
  expect(table.parentElement?.getAttribute('role')).toBe('region');
  expect(table.parentElement?.getAttribute('aria-label')).toBe('表格，可横向滚动');
  expect(table.parentElement?.tabIndex).toBe(0);
});

it('renders nested task lists as read-only checked and unchecked items', async () => {
  const user = userEvent.setup();
  const content = '- [x] 检查配置\n  - [ ] 验证 Windows\n\n- 普通条目';
  const { container } = render(<Conversation compacted={false} messages={[{
    id: 'tasks', type: 'ai', content, provisional: false, state: 'complete',
  }]} />);
  const inputs = screen.getAllByRole<HTMLInputElement>('checkbox');
  expect(inputs.map(input => [input.checked, input.disabled])).toEqual([[true, true], [false, true]]);
  expect(container.querySelector('li ul li input')).toBe(inputs[1]);
  await user.click(inputs[1]);
  expect(inputs[1].checked).toBe(false);
  expect(screen.getByText('普通条目').classList.contains('task-list-item')).toBe(false);
});

it('retains table DOM, keyboard focus and horizontal position across conversation scroll updates', () => {
  const { container } = render(<Conversation compacted={false} messages={[{
    id: 'scroll-table', type: 'ai', provisional: false, state: 'complete',
    content: '| 检查项 |\n| --- |\n| 内容 |',
  }]} />);
  const scroll = container.firstElementChild as HTMLDivElement;
  Object.defineProperties(scroll, { scrollHeight: { value: 1000 }, clientHeight: { value: 100 } });
  const region = screen.getByRole('region', { name: '表格，可横向滚动' });
  region.focus();
  region.scrollLeft = 40;
  scroll.scrollTop = 150;
  fireEvent.scroll(scroll);
  expect(screen.getByRole('region', { name: '表格，可横向滚动' })).toBe(region);
  expect(document.activeElement).toBe(region);
  expect(region.scrollLeft).toBe(40);
});

it('strikes only double tildes and preserves literal Markdown inside code', () => {
  const { container } = render(<Conversation compacted={false} messages={[{
    id: 'strike', type: 'ai', provisional: false, state: 'complete',
    content: '~~旧方案~~ 和 ~普通波浪号~\n\n`~~行内原文~~`\n\n```text\n- [x] 原始代码\n```',
  }]} />);
  expect(container.querySelector('del')?.textContent).toBe('旧方案');
  expect(container.querySelectorAll('del')).toHaveLength(1);
  expect(container.textContent).toContain('~普通波浪号~');
  expect(container.querySelector('code')?.textContent).toBe('~~行内原文~~');
  expect(container.querySelector('pre')?.textContent).toBe('- [x] 原始代码\n');
  expect(screen.queryByRole('checkbox')).toBeNull();
});

it('preserves the exact Markdown source when copying a rendered answer', async () => {
  const user = userEvent.setup();
  const content = '- [x] 完成\n\n~~旧方案~~';
  render(<Conversation compacted={false} messages={[{
    id: 'copy', type: 'ai', content, provisional: false, state: 'complete',
  }]} />);
  expect(screen.getByRole('checkbox')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: '复制回答' }));
  expect(await navigator.clipboard.readText()).toBe(content);
  expect(screen.getByText('已复制')).toBeTruthy();
});

it('renders human and unfinished model Markdown while keeping tool and summary text literal', () => {
  const content = '- [x] 完成\n\n~~旧方案~~';
  const { container } = render(<Conversation compacted={false} messages={[
    { id: 'human', type: 'human', content, provisional: false, state: 'complete' },
    { id: 'draft', type: 'ai', content, provisional: true, state: 'unconfirmed' },
    { id: 'tool', type: 'tool', content, provisional: false, state: 'complete' },
    { id: 'summary', type: 'human', content, provisional: false, state: 'complete', is_context_summary: true },
  ]} />);
  const rows = container.querySelectorAll('article');
  for (const row of Array.from(rows).slice(0, 2)) {
    expect(row.querySelector('input[type="checkbox"]')).toBeTruthy();
    expect(row.querySelector('del')?.textContent).toBe('旧方案');
  }
  for (const row of Array.from(rows).slice(2)) {
    expect(row.querySelector('input, del')).toBeNull();
    expect(row.textContent).toContain('- [x] 完成');
    expect(row.textContent).toContain('~~旧方案~~');
  }
  expect(screen.getByText('未完成内容')).toBeTruthy();
});

it('applies existing HTML, URL and image restrictions inside GFM tables', () => {
  const { container } = render(<Conversation compacted={false} messages={[{
    id: 'unsafe', type: 'ai', provisional: false, state: 'complete',
    content: '| 内容 |\n| --- |\n| [危险](javascript:alert%281%29) <img src="https://example.com/private" onerror="alert(1)"> ![占位](https://example.com/image.png) |\n\nhttps://example.com/docs',
  }]} />);
  expect(screen.getByRole('table')).toBeTruthy();
  expect(container.querySelector('img, script')).toBeNull();
  expect(container.querySelector('[onerror]')).toBeNull();
  expect([...container.querySelectorAll('a')].some(link => /^javascript:/i.test(link.getAttribute('href') ?? ''))).toBe(false);
  expect(screen.getByText('[图片：占位]')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'https://example.com/docs' }).getAttribute('rel')).toContain('noopener');
});

it('isolates same-named footnotes across messages and root or child Conversation instances', () => {
  const content = '引用[^1]\n\n[^1]: 说明';
  const messages = ['one', 'two'].map(id => ({ id, type: 'ai', content, provisional: false, state: 'complete' as const }));
  const { container } = render(<><Conversation compacted={false} messages={messages} />
    <Conversation compacted={false} messages={messages} /></>);
  const notes = [...container.querySelectorAll<HTMLElement>('li[id]')];
  expect(notes).toHaveLength(4);
  expect(new Set(notes.map(note => note.id)).size).toBe(4);
  for (const article of container.querySelectorAll('article')) {
    const reference = article.querySelector('a[data-footnote-ref]');
    expect(reference?.getAttribute('href')).toBe(`#${article.querySelector('li[id]')?.id}`);
  }
});

it('separates left-side model replies without a speaker name and retains unfinished status', () => {
  const { container } = render(<Conversation compacted={false} messages={[
    { id: 'user', type: 'human', content: '人类消息', provisional: false, state: 'complete' },
    { id: 'answer', type: 'ai', content: '模型回答', provisional: false, state: 'complete' },
    { id: 'partial', type: 'ai', content: '尚未完成', provisional: true, state: 'unconfirmed' },
  ]} />);
  expect(container.textContent).not.toContain('Naiyue');
  const rows = container.querySelectorAll('article');
  expect(rows[0].querySelector('hr')).toBeNull();
  expect(rows[1].firstElementChild?.tagName).toBe('HR');
  expect(rows[2].firstElementChild?.tagName).toBe('HR');
  expect(container.querySelectorAll('hr')).toHaveLength(2);
  expect(container.textContent).toContain('未完成内容');
});
