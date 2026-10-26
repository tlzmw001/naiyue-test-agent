import { afterEach, expect, it, vi } from 'vitest';
import { WebApi } from './api';
import type { MessageRow } from './types';

afterEach(() => vi.unstubAllGlobals());
const row: MessageRow = { id: 'saved', type: 'ai', content: 'preview', provisional: false, state: 'complete',
  sequence: 2, revision: 3, detail: { sequence: 2, revision: 3 }, content_truncated: true };
const serialized = (content: unknown, id = 'saved') => JSON.stringify({ type: 'ai', data: { id, content } });

it('loads bounded JSON pages by server offsets with session, child, and revision scope', async () => {
  const source = '```python\n' + 'print("中文😀")\n'.repeat(2000) + 'END\n```';
  const points = Array.from(serialized(source));
  const fetcher = vi.fn().mockImplementation(async (path: string) => {
    const url = new URL(path, 'http://localhost');
    const offset = Number(url.searchParams.get('offset'));
    const chunk = points.slice(offset, offset + 257).join('');
    const next = offset + Array.from(chunk).length;
    return new Response(JSON.stringify({ revision: 3, characters: points.length, body_json_chunk: chunk,
      next_offset: next, complete: next === points.length }));
  });
  vi.stubGlobal('fetch', fetcher);
  const controller = new AbortController();
  const value = await new WebApi('synthetic-access').messageContent('a/b', row, 7, 'child/one', controller.signal);
  expect(value).toBe(source);
  expect(fetcher.mock.calls.length).toBeGreaterThan(2);
  for (const [path, options] of fetcher.mock.calls) {
    const url = new URL(path, 'http://localhost');
    expect(url.pathname).toBe('/api/v1/scopes/chats/sessions/a%2Fb/messages/2/content');
    expect(url.searchParams.get('agent_id')).toBe('child/one');
    expect(url.searchParams.get('revision')).toBe('3');
    expect(url.searchParams.get('conversation_revision')).toBe('7');
    expect(options.signal).toBe(controller.signal);
    expect(options.headers.Authorization).toBe('Bearer synthetic-access');
    expect(options.method).toBe('GET');
  }
});

it('uses the existing text projection for multimodal content and preserves an empty body', async () => {
  for (const [input, expected] of [
    [[{ type: 'text', text: 'shown' }, { type: 'image_url', image_url: { url: 'PRIVATE_IMAGE' } }], 'shown'],
    ['', ''],
  ] as const) {
    const body = serialized(input);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 3,
      characters: Array.from(body).length, body_json_chunk: body, next_offset: Array.from(body).length, complete: true }))));
    expect(await new WebApi('synthetic-access').messageContent('s', row, 0)).toBe(expected);
  }
});

it.each([
  { revision: 4 }, { next_offset: 0, complete: false }, { characters: 999, complete: true },
  { body_json_chunk: '{broken' }, { body_json_chunk: serialized('complete', 'other') },
])('rejects inconsistent or misidentified saved content without looping: %j', async changes => {
  const body = serialized('complete');
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 3, characters: body.length,
    body_json_chunk: body, next_offset: body.length, complete: true, ...changes })));
  vi.stubGlobal('fetch', fetcher);
  await expect(new WebApi('synthetic-access').messageContent('s', row, 0)).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it('stops after a stale history response and preserves its error code', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'history_cursor_stale' } }), { status: 409 }));
  vi.stubGlobal('fetch', fetcher);
  await expect(new WebApi('synthetic-access').messageContent('s', row, 2)).rejects.toMatchObject({ code: 'history_cursor_stale' });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
