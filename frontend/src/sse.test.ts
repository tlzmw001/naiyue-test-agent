import { describe, expect, it } from 'vitest';
import { decodeSSE } from './sse';

async function* bytes(text: string) {
  for (const value of new TextEncoder().encode(text)) yield new Uint8Array([value]);
}

describe('SSE transport', () => {
  it('reconstructs UTF-8, CRLF frames and ignores keepalive comments', async () => {
    const text = ': ping\r\n\r\nevent: runtime\r\nid: 7\r\ndata: {"text":"中文😀"}\r\n\r\n';
    const frames = [];
    for await (const frame of decodeSSE(bytes(text))) frames.push(frame);
    expect(frames).toEqual([{ event: 'runtime', id: '7', data: { text: '中文😀' } }]);
  });
  it('rejects truncated frames rather than silently confirming them', async () => {
    await expect(async () => {
      for await (const frame of decodeSSE(bytes('event: runtime\ndata: {"x":1}'))) void frame;
    }).rejects.toThrow('stream_truncated');
  });
  it('preserves the explicit resync event', async () => {
    const frames = [];
    for await (const frame of decodeSSE(bytes('event: resync_required\ndata: {"code":"resync_required"}\n\n')))
      frames.push(frame);
    expect(frames[0].event).toBe('resync_required');
  });
});

it('rejects an oversized complete frame even when received in a single chunk', async () => {
  async function* oversized() {
    yield new TextEncoder().encode('data: ' + JSON.stringify({ text: 'x'.repeat(1024 * 1024) }) + '\n\n');
  }
  await expect(async () => {
    for await (const value of decodeSSE(oversized())) void value;
  }).rejects.toThrow('frame_too_large');
});
