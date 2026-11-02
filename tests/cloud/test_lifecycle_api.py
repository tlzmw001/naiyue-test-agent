import asyncio

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import owner_headers
from lifecycle_helpers import Backend, command, config, register


def test_owner_lifecycle_routes_and_task_route_separation(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            async with open_cloud_backend(runtime, backend=Backend()):
                record = register(runtime)
                owner, task = create_cloud_apps(runtime)
                route = '/api/cloud/v1/tasks/' + record.task_id + '/commands'
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner), base_url=runtime.settings.origin,
                        headers=owner_headers(runtime)) as client:
                    request = command(record)
                    response = await client.post(route, json=request)
                    assert response.status_code == 200 and response.json()['status'] == 'succeeded'
                    assert (await client.post(route, json=request)).json() == response.json()
                    assert (await client.post('/api/cloud/v1/commands/' + request['command_id'] + '/reconcile', json={})).json() == response.json()
                    metadata = (await client.get('/api/cloud/v1/tasks/' + record.task_id)).json()
                    assert metadata['environment_phase'] == 'backend_ready' and metadata['lifecycle'] == 'starting'
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task), base_url='http://' + runtime.settings.task_listen) as client:
                    assert (await client.post(route, json=request)).status_code == 404
        asyncio.run(run())


def test_cancelled_http_waiter_does_not_cancel_backend_producer(tmp_path):
    class Delayed(Backend):
        async def create(self, *args):
            entered.set()
            await release.wait()
            return await super().create(*args)
    entered, release = asyncio.Event(), asyncio.Event()
    with open_cloud_runtime(config(tmp_path)) as runtime:
        async def run():
            backend = Delayed()
            async with open_cloud_backend(runtime, backend=backend):
                record = register(runtime)
                request = command(record)
                pending = asyncio.create_task(runtime.lifecycle.command(record.task_id, request))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError): await pending
                finally: release.set()
                done = await runtime.lifecycle.command(record.task_id, request)
                assert done.status == 'succeeded'
                assert sum(c[0] == 'create' for c in backend.calls) == 1
        asyncio.run(run())
