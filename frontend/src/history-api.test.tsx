import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import { applyEvent, validateEvent } from './events';
import Conversation from './components/Conversation';
import type { MessageRow, RunView } from './types';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const row = (id: string, content: string): MessageRow => ({ id, content, type: 'ai', state: 'complete', provisional: false });

it('opens explicitly and rebuilds the local delta baseline without another GET', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    messages: [{ ...row('draft', 'prefix'), provisional: true, delta_index: 2 }],
    live: { session_id: 's', run_id: 'r', status: 'running', last_sequence: 5,
      messages: [], messages_location: 'session' },
  })));
  vi.stubGlobal('fetch', fetcher);
  const session = await new WebApi('synthetic-access').prepare('s');
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('/api/sessions/s/prepare');
  expect(fetcher.mock.calls[0][1].method).toBe('POST');
  expect(session.live?.messages).toBe(session.messages);
  const next = applyEvent(session.live!, validateEvent({ version: 1, session_id: 's', run_id: 'r',
    sequence: 6, event_type: 'message_delta', data: { message_id: 'draft', index: 3, text: ' tail' } }));
  expect(next.messages[0].content).toBe('prefix tail');
});

it('keeps GET read-only and passes opaque child cursors without executable thread names', async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ messages: [], live: null }))));
  vi.stubGlobal('fetch', fetcher);
  const api = new WebApi('synthetic-access');
  await api.session('a/b');
  await api.messages('a/b', 'opaque+/cursor', 'actual-child');
  expect(fetcher.mock.calls[0][0]).toBe('/api/sessions/a%2Fb');
  expect(fetcher.mock.calls[0][1].method).toBe('GET');
  const url = new URL(fetcher.mock.calls[1][0], 'http://localhost');
  expect(url.searchParams.get('before')).toBe('opaque+/cursor');
  expect(url.searchParams.get('agent_id')).toBe('actual-child');
  expect(url.searchParams.has('thread_id')).toBe(false);
});

it('uses explicit parent identity when a result is separated from its tool batch', () => {
  const call = { id: 'reused', name: 'read', args: { path: 'note' } };
  render(<Conversation compacted={false} messages={[
    { ...row('owner', ''), tool_calls: [call] },
    row('later', 'another reply'),
    { ...row('result', 'saved result'), type: 'tool', tool_call_id: 'reused', status: 'success', parent_message_id: 'owner' },
  ]} />);
  const article = screen.getByText('read').closest('article')!;
  expect(within(article).getByText('saved result')).toBeDefined();
  expect(document.querySelectorAll('article')).toHaveLength(2);
});

it('does not append a delta to a bounded draft preview as if it were complete', () => {
  const view = { session_id: 's', run_id: 'r', last_sequence: 5, status: 'running',
    messages: [{ ...row('draft', 'preview'), provisional: true, delta_index: 2,
      content_unavailable: true, content_truncated: true }] } as RunView;
  const next = applyEvent(view, validateEvent({ version: 1, session_id: 's', run_id: 'r', sequence: 6,
    event_type: 'message_delta', data: { message_id: 'draft', index: 3, text: 'tail' } }));
  expect(next.messages[0]).toMatchObject({ content: 'preview', content_unavailable: true,
    content_truncated: true, delta_index: 3 });
  const complete = applyEvent(next, validateEvent({ version: 1, session_id: 's', run_id: 'r', sequence: 7,
    event_type: 'message_completed', data: { snapshot_json: JSON.stringify(row('draft', 'complete original')) } }));
  expect(complete.messages[0].content).toBe('complete original');
  expect(complete.messages[0].content_unavailable).toBeUndefined();
});

it('sends the displayed conversation revision with an ordinary task', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: 'r', session_id: 's' })));
  vi.stubGlobal('fetch', fetcher);
  await new WebApi('synthetic-access').start('s', 'task', '00000000-0000-4000-8000-000000000001', 3);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ text: 'task', request_id: '00000000-0000-4000-8000-000000000001', expected_conversation_revision: 3 });
});
