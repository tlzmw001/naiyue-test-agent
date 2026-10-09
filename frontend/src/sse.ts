export interface SseFrame {
  event: string;
  id?: string;
  data: unknown;
}

function frame(text: string): SseFrame | null {
  let event = 'message';
  let id: string | undefined;
  const data: string[] = [];
  for (const line of text.split('\n')) {
    if (!line || line.startsWith(':')) continue;
    const split = line.indexOf(':');
    const key = split < 0 ? line : line.slice(0, split);
    const value = split < 0 ? '' : line.slice(split + 1).replace(/^ /, '');
    if (key === 'event') event = value;
    else if (key === 'id') id = value;
    else if (key === 'data') data.push(value);
  }
  if (!data.length) return null;
  return { event, ...(id === undefined ? {} : { id }), data: JSON.parse(data.join('\n')) };
}

export async function* decodeSSE(source: AsyncIterable<Uint8Array>): AsyncGenerator<SseFrame> {
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '';
  for await (const chunk of source) {
    buffer = (buffer + decoder.decode(chunk, { stream: true })).replace(/\r\n/g, '\n');
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const text = buffer.slice(0, end);
      if (new TextEncoder().encode(text).length > 1024 * 1024) throw new Error('frame_too_large');
      const value = frame(text);
      buffer = buffer.slice(end + 2);
      if (value) yield value;
    }
    if (new TextEncoder().encode(buffer).length > 1024 * 1024) throw new Error('frame_too_large');
  }
  buffer += decoder.decode();
  if (buffer.trim()) throw new Error('stream_truncated');
}

export async function* responseBytes(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return;
      yield value;
    }
  } finally {
    try { await reader.cancel(); }
    catch { console.warn('stream_reader_detach_failed'); }
    reader.releaseLock();
  }
}
