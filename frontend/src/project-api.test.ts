import { afterEach, expect, it, vi } from 'vitest';
import { WebApi, ApiError, errorText } from './api';

it('explains expired project approval without suggesting reuse of the old grant', () => {
  expect(errorText(new ApiError('approval_expired'))).toBe('项目位置或登记信息已变化，原审批已过期。请结束旧批次，再重新提出任务进行审查。');
});

afterEach(() => vi.unstubAllGlobals());
it('pins service-level metadata and new scoped clients to the same credentials and instance', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response('[]'));
  vi.stubGlobal('fetch', fetch);
  const api = new WebApi('synthetic', 'chats', 'f'.repeat(32));
  await api.directory('project/id');
  await api.projects();
  await api.forScope('another').sessions();
  expect(fetch.mock.calls.map(call => call[0])).toEqual(['/api/v1/navigation/project%2Fid/sessions', '/api/v1/projects', '/api/v1/scopes/another/sessions']);
  for (const [, options] of fetch.mock.calls) {
    expect(options.headers.Authorization).toBe('Bearer synthetic');
    expect(options.headers['X-Service-Instance']).toBe('f'.repeat(32));
    expect(options.redirect).toBe('error');
  }
  expect(api.scopeId).toBe('chats');
});

it('uses service-level folder routes, observed identities and stable mutation IDs', async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  const api = new WebApi('synthetic', 'chats', 'f'.repeat(32)), identity = 'a'.repeat(64);
  await api.browseDirectories({ path: 'C:\\Projects\\one&two', show_hidden: true, offset: 100 });
  const url = new URL(fetch.mock.calls[0][0], 'http://local.invalid');
  expect(url.pathname).toBe('/api/v1/directories'); expect(url.searchParams.get('path')).toBe('C:\\Projects\\one&two');
  expect(url.searchParams.get('show_hidden')).toBe('true'); expect(url.searchParams.get('offset')).toBe('100');
  await api.addLocalProject('/chosen', identity, 'same-request');
  await api.addLocalProject('/chosen', identity, 'same-request');
  await api.createLocalProject('/parent', 'folder', identity, 'create-request');
  await api.reconcileProject('original-operation');
  expect(fetch.mock.calls[1][0]).toBe('/api/v1/projects/local');
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ root: '/chosen', trusted: true, expected_directory: identity });
  expect(fetch.mock.calls[1][1].headers['X-Request-ID']).toBe(fetch.mock.calls[2][1].headers['X-Request-ID']);
  expect(fetch.mock.calls[3][0]).toBe('/api/v1/projects/create');
  expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual({ parent: '/parent', folder_name: 'folder', trusted: true, expected_directory: identity });
  expect(JSON.parse(fetch.mock.calls[4][1].body)).toEqual({ operation_id: 'original-operation' });
});
