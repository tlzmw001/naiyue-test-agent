import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.application.cloud.backend import OpenSandboxBackend
from naiyue_test_agent.application.cloud.settings import BackendSettings
from naiyue_test_agent.contracts.cloud_lifecycle import BackendFailure, EnvironmentOperation
from lifecycle_helpers import template


def backend_config():
    return BackendSettings(endpoint='http://127.0.0.1:28701', backend_id='fixture-daemon', api_key='backend-test-key')


def info(ident, metadata=None):
    now = datetime.now(timezone.utc).isoformat()
    return dict(id=ident, status={'state': 'Running'}, entrypoint=['/bin/sleep', 'infinity'],
        createdAt=now, image={'uri': template().image_id}, metadata=metadata or {})


def test_sdk_pagination_and_missing_are_real_protocol_calls_without_retries():
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'DELETE': return httpx.Response(204)
        if '/sandboxes/missing' in request.url.path:
            return httpx.Response(404, json={'code': 'SANDBOX_NOT_FOUND', 'message': 'PRIVATE'})
        if request.url.path.endswith('/sandboxes'):
            page = int(request.url.params.get('page', '1'))
            return httpx.Response(200, json={'items': [info('one' if page == 1 else 'two')],
                'pagination': {'page': page, 'pageSize': 1, 'totalItems': 2, 'totalPages': 2, 'hasNextPage': page == 1}})
        return httpx.Response(200, json=info('one'))
    async def run():
        backend = await OpenSandboxBackend.open(backend_config(), transport=httpx.MockTransport(handler))
        try:
            assert await backend.get('missing') is None
            assert len(await backend.find({'nyt-instance': str(uuid4())})) == 2
            await backend.kill('one')
        finally: await backend.close()
    asyncio.run(run())
    assert all(r.headers['OPEN-SANDBOX-API-KEY'] == 'backend-test-key' for r in requests)
    assert sum(r.method == 'DELETE' for r in requests) == 1


def test_initial_create_rejection_is_distinguished_from_unknown_failure(caplog):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'POST'
        body = json.loads(request.content)
        assert body['timeout'] is None
        assert body['image']['uri'] == template().image_id
        assert 'backend-test-key' not in request.content.decode()
        return httpx.Response(403, json={'code': 'FORBIDDEN', 'message': 'PRIVATE_UPSTREAM_BODY'})
    async def run():
        backend = await OpenSandboxBackend.open(backend_config(), transport=httpx.MockTransport(handler))
        operation = EnvironmentOperation(command_id=str(uuid4()), task_id=uuid4().hex, generation=1,
            kind='start', phase='dispatched', backend_id=backend.backend_id, template=template(), instance_id=str(uuid4()))
        try:
            with pytest.raises(BackendFailure) as caught:
                await backend.create(operation, [], {'cpu': '2', 'memory': '2Gi'})
            assert caught.value.rejected
        finally: await backend.close()
    asyncio.run(run())
    assert len(calls) == 1
    assert 'PRIVATE_UPSTREAM_BODY' not in caplog.text and 'backend-test-key' not in caplog.text


def test_runner_bootstrap_mount_name_matches_server_dns_label_contract():
    from naiyue_test_agent.contracts.cloud_lifecycle import MountSpec
    import re
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(403,json={'code':'FORBIDDEN','message':'controlled terminal response'})
    async def run():
        backend=await OpenSandboxBackend.open(backend_config(),transport=httpx.MockTransport(handler))
        operation=EnvironmentOperation(command_id=str(uuid4()),task_id=uuid4().hex,generation=1,
            kind='start',phase='dispatched',backend_id=backend.backend_id,template=template(),instance_id=str(uuid4()))
        try:
            with pytest.raises(BackendFailure):await backend.create(operation,[MountSpec(
                source='/tmp/controlled-bootstrap',destination='/run/nyt',read_only=True)],{'cpu':'2','memory':'2Gi'})
        finally:await backend.close()
    asyncio.run(run())
    assert len(requests)==1
    body=requests[0]
    assert body['entrypoint']==['/opt/nyt/runner/bin/python','-I','/opt/nyt/runner-entry.py']
    assert body['volumes'][0]['mountPath']=='/run/nyt' and body['volumes'][0]['readOnly']
    assert re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',body['volumes'][0]['name'])


def test_successful_create_then_endpoint_rejection_remains_unknown(caplog):
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == 'POST':
            return httpx.Response(202, json=info('already-created'))
        return httpx.Response(403, json={'code': 'FORBIDDEN', 'message': 'PRIVATE_ENDPOINT_BODY'})
    async def run():
        backend = await OpenSandboxBackend.open(backend_config(), transport=httpx.MockTransport(handler))
        operation = EnvironmentOperation(command_id=str(uuid4()), task_id=uuid4().hex, generation=1,
            kind='start', phase='dispatched', backend_id=backend.backend_id, template=template(), instance_id=str(uuid4()))
        try:
            with pytest.raises(BackendFailure) as caught:
                await backend.create(operation, [], {'cpu': '2', 'memory': '2Gi'})
            assert not caught.value.rejected
        finally: await backend.close()
    asyncio.run(run())
    assert sum(r.method == 'POST' for r in calls) == 1
    assert any(r.method == 'GET' for r in calls)
    assert 'PRIVATE_ENDPOINT_BODY' not in caplog.text


def test_owned_transport_close_failure_is_visible():
    class BrokenClose(httpx.MockTransport):
        async def aclose(self):
            raise OSError('controlled transport close failure')
    async def run():
        backend = await OpenSandboxBackend.open(backend_config(), transport=BrokenClose(lambda r: httpx.Response(200)))
        with pytest.raises(OSError, match='controlled transport close failure'):
            await backend.close()
    asyncio.run(run())
