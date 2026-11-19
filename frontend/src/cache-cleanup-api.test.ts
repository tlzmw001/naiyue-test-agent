import { expect, test, vi } from 'vitest';
import { WebApi } from './api';

test('cleanup uses the global authenticated maintenance route', async () => {
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));
  try {
    const api = new WebApi('token', 'project', 'instance').cacheCleanup();
    await api.execute('request', ['cloud_inputs']);
    expect(fetcher).toHaveBeenCalledWith('/api/v1/maintenance/cache-cleanup', expect.objectContaining({
      method: 'POST', body: '{"request_id":"request","categories":["cloud_inputs"]}',
      headers: expect.objectContaining({ Authorization: 'Bearer token', 'X-Request-ID': 'request', 'X-Service-Instance': 'instance' }),
    }));
  } finally {
    fetcher.mockRestore();
  }
});

test('an authenticated launch works when the browser cannot persist the connection', async () => {
  const { browserConnection } = await import('./api');
  const token = 't'.repeat(35), instanceId = 'a'.repeat(32);
  const storage = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('unavailable'); });
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    window.location.hash = `access_token=${token}&scope_id=chats&instance_id=${instanceId}`;
    expect(browserConnection()).toEqual({ token, instanceId, scopeId: 'chats' });
    expect(warn).toHaveBeenCalled();
  } finally {
    window.location.hash = '';
    warn.mockRestore();
    storage.mockRestore();
  }
});
