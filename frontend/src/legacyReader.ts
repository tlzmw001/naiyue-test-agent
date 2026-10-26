/** Read-only legacy recovery uses the existing bounded terminal history protocol. */
import { ApiError, type BrowserConnection } from './api';
import type { MessageRow, SessionRow } from './types';

export class LegacyReader {
  constructor(private connection: BrowserConnection) {}
  private async get(path: string, values: Record<string, string>, signal: AbortSignal) {
    const query = new URLSearchParams({ database_path: this.connection.legacyDatabase!, ...values });
    const response = await fetch('/api/v1/terminal/legacy' + path + '?' + query, { signal, redirect: 'error', headers: {
      Authorization: 'Bearer ' + this.connection.token, 'X-Service-Instance': this.connection.instanceId,
    } });
    if (!response.ok) throw new ApiError((await response.json()).error?.code ?? 'http_failed');
    return response.json();
  }
  sessions(signal: AbortSignal): Promise<{ sessions: SessionRow[]; root: string; execution_root_error: { reason: string } }> {
    return this.get('', {}, signal);
  }
  history(id: string, signal: AbortSignal): Promise<{ message_count: number; checkpoint_id: string }> {
    return this.get('/history', { session_id: id }, signal);
  }
  async message(id: string, index: number, checkpoint: string, signal: AbortSignal): Promise<MessageRow> {
    let offset = 0, hash: string | undefined;
    const parts: string[] = [];
    while (true) {
      const part = await this.get('/message', { session_id: id, index: String(index),
        checkpoint, offset: String(offset) }, signal);
      if (typeof part.text !== 'string' || !part.text || !Number.isSafeInteger(part.next_offset) ||
          part.next_offset !== offset + Array.from(part.text).length || typeof part.complete !== 'boolean' ||
          typeof part.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(part.sha256) || hash && hash !== part.sha256)
        throw new ApiError('history_content_invalid');
      parts.push(part.text); hash = part.sha256; offset = part.next_offset;
      if (part.complete) break;
    }
    const text = parts.join('');
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    if (Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('') !== hash)
      throw new ApiError('history_content_invalid');
    const row = JSON.parse(text);
    if (typeof row.id !== 'string' || !['human', 'ai', 'tool', 'system'].includes(row.type) || typeof row.content !== 'string')
      throw new ApiError('history_content_invalid');
    return row;
  }
}
