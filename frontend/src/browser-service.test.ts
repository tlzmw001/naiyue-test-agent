import { afterEach, expect, it, vi } from 'vitest';
import { browserConnection, WebApi } from './api';

afterEach(() => { sessionStorage.clear(); history.replaceState(null, '', '/'); vi.unstubAllGlobals(); });
const scope = '12345678-1234-4234-8234-123456789abc';
const instance = 'a'.repeat(32);
it('takes the whole private fragment once and preserves scope on reload', () => {
  history.replaceState(null, '', `/#access_token=${'x'.repeat(40)}&scope_id=${scope}&instance_id=${instance}`);
  const connection = browserConnection();
  expect(connection).toEqual({ token: 'x'.repeat(40), scopeId: scope, instanceId: instance });
  expect(location.hash).toBe('');
  expect(browserConnection()).toEqual(connection);
});
it('does not silently pick a project when bootstrap is incomplete', () => {
  history.replaceState(null, '', '/#access_token=secret');
  expect(browserConnection()).toEqual({ token: '', scopeId: '', instanceId: '' });
  expect(location.hash).toBe('');
});
it('binds all writes to the scope and instance with one consistent command UUID', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: 'r' })));
  vi.stubGlobal('fetch', fetcher);
  const api = new WebApi('secret', scope, instance);
  const id = crypto.randomUUID();
  await api.start('s', 'question', id);
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toBe(`/api/v1/scopes/${scope}/sessions/s/runs`);
  expect(options.headers['X-Request-ID']).toBe(id);
  expect(options.headers['X-Service-Instance']).toBe(instance);
  expect(options.redirect).toBe('error');
});
it('gives bodyless mutations an ID and never retries failed writes', async () => {
  const fetcher = vi.fn().mockRejectedValue(new TypeError('disconnected'));
  vi.stubGlobal('fetch', fetcher);
  await expect(new WebApi('secret', scope, instance).deleteSession('s')).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][1].headers['X-Request-ID']).toMatch(/^[0-9a-f-]{36}$/);
});
