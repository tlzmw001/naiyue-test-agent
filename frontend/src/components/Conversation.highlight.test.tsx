import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { HIGHLIGHT_TIMEOUT_MS } from '../code-highlight-client';
import Conversation from './Conversation';

class ControlledWorker {
  static instances: ControlledWorker[] = [];
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessageerror: ((event: Event) => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() { ControlledWorker.instances.push(this); }
  finish(parts: { text: string; classes: string }[]) {
    this.onmessage?.({ data: { id: this.postMessage.mock.calls.at(-1)?.[0].id, parts } } as MessageEvent);
  }
}
beforeEach(() => { ControlledWorker.instances = []; vi.stubGlobal('Worker', ControlledWorker); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks(); });
const view = (content: string) => <Conversation compacted={false} messages={[
  { id: 'm', type: 'ai', content, provisional: false, state: 'complete' },
]} />;

it('shows full code immediately, then colors it without replacing code, focus or scroll', async () => {
  const text = 'return "中文😀"\n' + '# retained\n'.repeat(2000);
  const { container } = render(view('```python\n' + text + '```'));
  const code = container.querySelector('pre code');
  const pre = screen.getByRole('region', { name: '代码，可横向滚动' });
  expect(code?.textContent).toBe(text);
  expect(code?.querySelector('span')).toBeNull();
  expect(ControlledWorker.instances).toHaveLength(1);
  pre.scrollLeft = 120; pre.focus();
  await act(async () => ControlledWorker.instances[0].finish([
    { text: 'return', classes: 'hljs-keyword' }, { text: text.slice(6), classes: '' },
  ]));
  expect(container.querySelector('pre code')).toBe(code);
  expect(code?.querySelector('.hljs-keyword')?.textContent).toBe('return');
  expect(code?.textContent).toBe(text);
  expect(pre.scrollLeft).toBe(120);
  expect(document.activeElement).toBe(pre);
});

it('does not resubmit an unchanged completed block when later content streams', async () => {
  const source = '```python\nreturn 1\n```\n\n';
  const { container, rerender } = render(view(source));
  expect(ControlledWorker.instances).toHaveLength(1);
  const worker = ControlledWorker.instances[0];
  await act(async () => worker.finish([{ text: 'return 1\n', classes: 'hljs-keyword' }]));
  const code = container.querySelector('pre code');
  const colored = code?.firstChild;
  rerender(view(source + 'additional prose'));
  expect(ControlledWorker.instances).toHaveLength(1);
  expect(worker.postMessage).toHaveBeenCalledTimes(1);
  expect(container.querySelector('pre code')).toBe(code);
  expect(code?.firstChild).toBe(colored);
});

it('cancels obsolete work and ignores a result from an older streaming body', async () => {
  const { container, rerender } = render(view('```python\nreturn "old"'));
  expect(ControlledWorker.instances).toHaveLength(1);
  const old = ControlledWorker.instances[0];
  rerender(view('```python\nreturn "new"'));
  expect(old.terminate).toHaveBeenCalled();
  expect(ControlledWorker.instances).toHaveLength(2);
  await act(async () => old.finish([{ text: 'STALE', classes: 'hljs-string' }]));
  expect(container.querySelector('pre code')?.textContent).toBe('return "new"\n');
  await act(async () => ControlledWorker.instances[1].finish([{ text: 'return "new"\n', classes: 'hljs-keyword' }]));
  expect(container.querySelector('.hljs-keyword')?.textContent).toBe('return "new"\n');
});

it.each(['', 'text', 'plaintext', 'txt'])('does not start a highlighter for plain code %s', language => {
  const { container } = render(view('```' + language + '\n<script>literal</script>\n```'));
  expect(container.querySelector('pre code')?.textContent).toBe('<script>literal</script>\n');
  expect(container.querySelector('script')).toBeNull();
  expect(ControlledWorker.instances).toHaveLength(0);
  expect(screen.getByText(language || 'text', { selector: 'span' })).toBeTruthy();
});

it('uses one worker for two code blocks and releases it when the queue finishes', async () => {
  const { container } = render(view('```python\nreturn 1\n```\n\n```js\nlet x = 2;\n```'));
  expect(ControlledWorker.instances).toHaveLength(1);
  const worker = ControlledWorker.instances[0];
  expect(worker.postMessage).toHaveBeenCalledTimes(1);
  await act(async () => worker.finish([{ text: 'return 1\n', classes: 'hljs-keyword' }]));
  expect(worker.postMessage).toHaveBeenCalledTimes(2);
  expect(worker.terminate).not.toHaveBeenCalled();
  await act(async () => worker.finish([{ text: 'let x = 2;\n', classes: 'hljs-keyword' }]));
  expect(container.querySelectorAll('.hljs-keyword')).toHaveLength(2);
  expect(worker.terminate).toHaveBeenCalledTimes(1);
});

it('removes an unmounted queued block before it starts', async () => {
  const first = '```python\nreturn 1\n```';
  const { rerender } = render(view(first + '\n\n```js\nlet x = 2;\n```'));
  const worker = ControlledWorker.instances[0];
  rerender(view(first));
  await act(async () => worker.finish([{ text: 'return 1\n', classes: 'hljs-keyword' }]));
  expect(worker.postMessage).toHaveBeenCalledTimes(1);
  expect(worker.terminate).toHaveBeenCalled();
});

it.each(['worker_error', 'worker_message_error', 'timeout', 'text_mismatch', 'render_budget', 'failed'])(
  'keeps complete plain code after %s and still processes the next block', async reason => {
    vi.useFakeTimers();
    const diagnostic = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { container } = render(view('```python\nreturn "sensitive body"\n```\n\n```js\nlet x = 2;\n```'));
    const first = ControlledWorker.instances[0];
    await act(async () => {
      if (reason === 'worker_error') first.onerror?.(new Event('error', { cancelable: true }));
      else if (reason === 'worker_message_error') first.onmessageerror?.(new Event('messageerror'));
      else if (reason === 'timeout') vi.advanceTimersByTime(HIGHLIGHT_TIMEOUT_MS);
      else if (reason === 'text_mismatch') first.finish([{ text: 'MISMATCH', classes: 'hljs-keyword' }]);
      else first.onmessage?.({ data: { id: first.postMessage.mock.calls[0][0].id, reason } } as MessageEvent);
    });
    expect(container.querySelectorAll('pre code')[0].textContent).toBe('return "sensitive body"\n');
    expect(container.querySelectorAll('pre code')[0].querySelector('span')).toBeNull();
    expect(diagnostic).toHaveBeenCalledWith('code_highlight_degraded', { reason });
    expect(JSON.stringify(diagnostic.mock.calls)).not.toContain('sensitive');
    const current = ControlledWorker.instances.at(-1)!;
    await act(async () => current.finish([{ text: 'let x = 2;\n', classes: 'hljs-keyword' }]));
    expect(container.querySelectorAll('pre code')[1].querySelector('.hljs-keyword')).not.toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  },
);

it('keeps plain code readable and logs a safe reason when Worker cannot start', () => {
  vi.stubGlobal('Worker', class { constructor() { throw new Error('private startup text'); } });
  const diagnostic = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const { container } = render(view('```python\nreturn "private code"\n```'));
  expect(container.querySelector('pre code')?.textContent).toBe('return "private code"\n');
  expect(diagnostic).toHaveBeenCalledWith('code_highlight_degraded', { reason: 'worker_start_failed' });
  expect(JSON.stringify(diagnostic.mock.calls)).not.toContain('private');
});

it('keeps both blocks readable and reports Worker unavailability once', () => {
  vi.stubGlobal('Worker', undefined);
  const diagnostic = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const { container } = render(view('```python\nreturn 1\n```\n\n```js\nlet x = 2;\n```'));
  expect([...container.querySelectorAll('pre code')].map(code => code.textContent)).toEqual(['return 1\n', 'let x = 2;\n']);
  expect(diagnostic).toHaveBeenCalledExactlyOnceWith('code_highlight_degraded', { reason: 'worker_unavailable' });
});

it('cancels a result when only the explicit language changes', async () => {
  const { container, rerender } = render(view('```python\nreturn 1\n```'));
  const old = ControlledWorker.instances[0];
  rerender(view('```js\nreturn 1\n```'));
  expect(old.terminate).toHaveBeenCalled();
  await act(async () => old.finish([{ text: 'return 1\n', classes: 'hljs-string' }]));
  expect(container.querySelector('pre code')?.className).toBe('language-js');
  expect(container.querySelector('.hljs-string')).toBeNull();
  expect(container.querySelector('pre code')?.textContent).toBe('return 1\n');
});
