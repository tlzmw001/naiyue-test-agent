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

it('sends result receive and analysis commands with local project/session and no report body', async () => {
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 200 }));
  const api = new WebApi('fixture-token', 'P', 'instance').cloudTasks();
  await api.receive('S', 'B', 'R', { command_id: 'C', artifact_ids: ['F'] });
  await api.receiveStatus('S', 'C'); await api.retryReceive('S', 'C'); await api.analysisDraft('S', 'C');
  expect(fetcher.mock.calls.map(([path]) => String(path))).toEqual([
    '/api/client/v1/cloud-tasks/B/results/R/receive?local_project_id=P',
    '/api/client/v1/cloud-receives/C?local_project_id=P&local_session_id=S',
    '/api/client/v1/cloud-receives/C/retry?local_project_id=P&local_session_id=S',
    '/api/client/v1/cloud-receives/C/analysis-draft?local_project_id=P&local_session_id=S',
  ]);
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ command_id: 'C', local_session_id: 'S', artifact_ids: ['F'] });
  expect(fetcher.mock.calls[2][1]?.body).toBe('{}'); expect(fetcher.mock.calls[3][1]?.body).toBe('{}');
  fetcher.mockRestore();
});

it('separates read-only delivery preparation from explicit confirmed execution', async () => {
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 200 }));
  const api = new WebApi('fixture-token', 'P', 'instance').cloudTasks();
  await api.delivery('S', { command_id: 'D', receive_id: 'C', action: 'export', destination_handle: 'P', name: 'result-copy' });
  await api.deliveryStatus('S', 'D'); await api.executeDelivery('S', 'D');
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    '/api/client/v1/cloud-deliveries?local_project_id=P',
    '/api/client/v1/cloud-deliveries/D?local_project_id=P&local_session_id=S',
    '/api/client/v1/cloud-deliveries/D/execute?local_project_id=P&local_session_id=S',
  ]);
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ command_id: 'D', local_session_id: 'S', receive_id: 'C', action: 'export', destination_handle: 'P', name: 'result-copy' });
  expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({ confirm: true }); fetcher.mockRestore();
});
