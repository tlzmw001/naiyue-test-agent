import { expect, it, vi } from 'vitest';
import { WebApi } from './api';

it('binds cloud calls to local project, session and service authentication', async () => {
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
  const api = new WebApi('fixture-token', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'instance');
  await api.cloudTasks().list('S');
  const [url, init] = fetcher.mock.calls[0];
  expect(String(url)).toContain('/api/client/v1/cloud-tasks?');
  expect(String(url)).toContain('local_session_id=S');
  expect(String(url)).toContain('local_project_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
  expect(init?.headers).toMatchObject({ Authorization: 'Bearer fixture-token', 'X-Service-Instance': 'instance' });
  fetcher.mockRestore();
});
