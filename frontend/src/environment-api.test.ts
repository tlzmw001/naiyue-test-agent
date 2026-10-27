import { afterEach, expect, it, vi } from 'vitest';
import { WebApi, errorText, ApiError } from './api';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('refreshes only the fixed service configuration and sends explicit selection fingerprints', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ session_id: 'S' })));
  vi.stubGlobal('fetch', fetch);
  const api = new WebApi('synthetic', '11111111-1111-1111-1111-111111111111', 'instance');
  await api.refreshEnvironments();
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/environments/refresh');
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({});
  expect(fetch.mock.calls[0][1].headers['X-Request-ID']).toBeTruthy();
  await api.createSelected('A', { execution: 'sandbox', model_id: 'A', include_untracked: [], requestId: 'original',
    profile_id: 'python-tests', expected_profile_revision: 'a'.repeat(64) });
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ model_id: 'A', execution: 'sandbox', include_untracked: [],
    profile_id: 'python-tests', expected_profile_revision: 'a'.repeat(64) });
  expect(fetch.mock.calls[1][1].headers['X-Request-ID']).toBe('original');
});
it('explains catalog failures and changed choices without showing raw codes', () => {
  expect(errorText(new ApiError('env_profile_changed'))).toContain('重新选择');
  expect(errorText(new ApiError('env_configuration_invalid'))).toContain('保留');
});
it('explains a missing local SSH client without suggesting local Docker installation', () => {
  const message = errorText(new ApiError('env_ssh_unavailable'));
  expect(message).toContain('OpenSSH');
  expect(message).not.toContain('env_ssh_unavailable');
  expect(message).not.toContain('Docker');
});
it('retains the same request ID when retrying a host session draft', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ session_id: 'S' })));
  vi.stubGlobal('fetch', fetch);
  const api = new WebApi('synthetic', 'chats');
  const choice = { execution: 'host' as const, model_id: 'A', include_untracked: [], requestId: 'host-request' };
  await api.createSelected('A', choice); await api.createSelected('A', choice);
  expect(fetch.mock.calls.map(call => call[1].headers['X-Request-ID'])).toEqual(['host-request', 'host-request']);
});
