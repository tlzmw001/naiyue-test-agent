import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import Conversation from './Conversation';

afterEach(cleanup);
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
