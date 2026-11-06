import asyncio
from uuid import uuid4

from langchain_core.messages import AIMessage

from naiyue_test_agent.cloud_runner.kernel import open_runner
from naiyue_test_agent.contracts.cloud_mcp import CloudMCPSettings
from naiyue_test_agent.model_access import ModelCatalog
from test_async_application import StreamingToolModel, call
from test_mcp_agent_tools import calls
from test_mcp_connections import http_server
from test_runner_execution_control import directories
from test_runner_kernel import bootstrap, request


def test_cloud_runner_uses_all_discovered_tools_without_selection(tmp_path):
    async def scenario():
        root, state = directories(tmp_path)
        audit = tmp_path / 'mcp.jsonl'
        model = StreamingToolModel(replies=[
            call('mcp__logs__echo', {'text': 'cloud'}), AIMessage(content='done'),
        ])
        async with http_server(audit, 'stateful', json_response=True) as url:
            settings = CloudMCPSettings.model_validate({'servers': {'logs': {'url': url}}})
            async with open_runner(bootstrap(), root, state, catalog=ModelCatalog('A', {'A': model})) as kernel:
                report = await kernel.mcp.apply(settings, None)
                assert report[0]['tools'] == ['mcp__logs__echo', 'mcp__logs__other']
                kernel.mcp.applied_revision = str(uuid4())
                command = request().model_copy(update={'mcp_revision': kernel.mcp.applied_revision})
                assert (await kernel.execute(command))['status'] == 'completed'
                assert [row['arguments'] for row in calls(audit)] == [{'text': 'cloud'}]
    asyncio.run(scenario())


def test_owner_refresh_is_fenced_and_reports_only_after_runner_applies(tmp_path):
    from uuid import uuid4
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from cloud_helpers import owner_headers
    from lifecycle_helpers import config
    from test_runner_control import client, connect, hello, prepared

    with open_cloud_runtime(config(tmp_path)) as runtime:
        b = prepared(runtime)
        with client(runtime) as http, connect(http, runtime, b) as ws:
            ws.send_json(hello(b) | {'capabilities': ['mcp:refresh']})
            welcome = ws.receive_json()
            fence = {key: welcome[key] for key in ('connection_id', 'generation', 'boot_id', 'state_epoch')}
            path = f'/api/cloud/v1/tasks/{b.task_id}/mcp'
            headers = owner_headers(runtime)
            refresh = {'refresh_id': str(uuid4())}
            assert http.post(path, json=refresh).status_code != 200
            assert http.post(path, headers=headers, json=refresh).json()['status'] == 'refreshing'
            frame = ws.receive_json()
            assert frame['refresh_id'] == refresh['refresh_id']
            assert http.get(path, headers=headers).json()['status'] == 'refreshing'
            ws.send_json(dict(kind='mcp_applied', refresh_id=str(uuid4()), digest=frame['digest'], services=[], **fence))
            ws.send_json(dict(kind='heartbeat', **fence))
            assert ws.receive_json()['kind'] == 'welcome'
            assert http.get(path, headers=headers).json()['status'] == 'refreshing'
            ws.send_json(dict(kind='mcp_applied', refresh_id=frame['refresh_id'], digest=frame['digest'], services=[], **fence))
            ws.send_json(dict(kind='heartbeat', **fence))
            assert ws.receive_json()['kind'] == 'welcome'
            assert http.get(path, headers=headers).json()['status'] == 'available'
            assert http.post(path, headers=headers, json=refresh).json()['status'] == 'available'


def tls_certificate(tmp_path, hostname):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256()))
    certfile, keyfile = tmp_path / 'certificate.pem', tmp_path / 'key.pem'
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return certfile, keyfile


def test_tunnel_keeps_host_sni_certificate_path_and_rejects_changed_target(tmp_path, monkeypatch):
    from urllib.parse import urlsplit
    from test_mcp_connections import facts

    hostname = 'private-mcp.example.test'
    tls = tls_certificate(tmp_path, hostname)
    monkeypatch.setenv('SSL_CERT_FILE', str(tls[0]))
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')

    async def scenario():
        root, state = directories(tmp_path)
        audit = tmp_path / 'tls.jsonl'
        model = StreamingToolModel(replies=[call('mcp__logs__echo', {'text': 'tunnel'}), AIMessage(content='done')])
        async with http_server(audit, 'stateful', json_response=True, tls=tls) as physical:
            address = f'https://{hostname}/mcp/'
            settings = CloudMCPSettings.model_validate({'servers': {'logs': {
                'url': address, 'route': 'windows_tunnel', 'network_slot': 'corp'}}})
            slot = dict(address=address, connect_host='127.0.0.1', connect_port=urlsplit(physical).port)
            routes = {'slots': {'corp': slot}}
            async with open_runner(bootstrap(), root, state, catalog=ModelCatalog('A', {'A': model})) as kernel:
                report = await kernel.mcp.apply(settings, routes)
                assert report[0]['status'] == 'available', report
                assert not kernel.mcp.names_for({'slots': {'corp': slot | {'address': 'https://wrong.example.test/'}}})
                kernel.mcp.applied_revision = str(uuid4())
                command = request().model_copy(update={'mcp_revision': kernel.mcp.applied_revision, 'services': routes})
                assert (await kernel.execute(command))['status'] == 'completed'
                assert [row['arguments'] for row in calls(audit)] == [{'text': 'tunnel'}]
                assert not kernel.mcp.resources._owners
                before = facts(audit)
                assert {row['sni'] for row in before if row['event'] == 'tls'} == {hostname}
                assert {(row['host'], row['path']) for row in before if row['event'] == 'http_request'} == {(hostname, '/mcp/')}
                wrong = address.replace(hostname, 'wrong.example.test')
                config = CloudMCPSettings.model_validate({'servers': {'logs': {
                    'url': wrong, 'route': 'windows_tunnel', 'network_slot': 'corp'}}})
                assert (await kernel.mcp.apply(settings, {'slots': {}}))[0]['error'] == 'mcp_tunnel_target_unavailable'
                assert (await kernel.mcp.apply(config, {'slots': {'corp': slot | {'address': wrong}}}))[0]['tools'] == []
                assert len([row for row in facts(audit) if row['event'] == 'http_request']) == len(
                    [row for row in before if row['event'] == 'http_request'])
    asyncio.run(scenario())


def test_refresh_reloads_only_mcp_section_and_survives_offline_runner(tmp_path):
    import json
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from cloud_helpers import owner_headers
    from lifecycle_helpers import config
    from test_runner_control import client, connect, hello, prepared

    path = tmp_path / 'coordinator.json'
    settings = {'servers': {'logs': {'url': 'https://mcp.example.test/mcp'}}}
    with open_cloud_runtime(config(tmp_path), settings_path=path) as runtime:
        path.write_text(json.dumps({'mcp': settings, 'backend': 'irrelevant during MCP reload'}))
        path.chmod(0o600)
        b = prepared(runtime)
        api = f'/api/cloud/v1/tasks/{b.task_id}'
        headers = owner_headers(runtime)
        with client(runtime) as http:
            intent = {'refresh_id': str(uuid4())}
            assert http.post(api + '/mcp', headers=headers, json=intent).json()['status'] == 'waiting_runner'
            with connect(http, runtime, b) as ws:
                ws.send_json(hello(b) | {'capabilities': ['mcp:refresh']})
                welcome = ws.receive_json()
                fence = {key: welcome[key] for key in ('connection_id', 'generation', 'boot_id', 'state_epoch')}
                frame = ws.receive_json()
                assert frame['kind'] == 'mcp_refresh' and frame['refresh_id'] == intent['refresh_id']
                assert frame['settings']['servers']['logs']['url'] == settings['servers']['logs']['url']
                report = [dict(server_id='logs', route='direct', tools=['mcp__logs__echo'], status='available')]
                ws.send_json(dict(kind='mcp_applied', refresh_id=frame['refresh_id'], digest=frame['digest'], services=report, **fence))
                ws.send_json(dict(kind='heartbeat', **fence))
                assert ws.receive_json()['kind'] == 'welcome'
                path.write_text(json.dumps({'mcp': {'servers': {'logs': {'url': 'https://u:p@mcp.example.test/'}}}}))
                rejected = http.post(api + '/mcp', headers=headers, json={'refresh_id': str(uuid4())})
                assert rejected.status_code == 422
                assert http.get(api + '/mcp', headers=headers).json()['services'][0]['tools'] == ['mcp__logs__echo']
                task = runtime.registry.get_task(b.task_id)
                run = dict(command_id=str(uuid4()), expected_revision=task.revision, expected_generation=task.generation,
                    message='work', model_id='A')
                assert http.post(api + '/runs', headers=headers, json=run).status_code == 202
                assert ws.receive_json()['command']['mcp_revision'] == intent['refresh_id']
                path.write_text(json.dumps({'mcp': {'servers': {}}}))
                assert http.post(api + '/mcp', headers=headers, json={'refresh_id': str(uuid4())}).json()['status'] == 'waiting_run'


def test_runner_reconnect_reuses_confirmed_directory_and_manual_refresh_uses_new_schema(tmp_path):
    import time
    from naiyue_test_agent.cloud_runner.transport import RunnerClient
    from naiyue_test_agent.contracts.cloud_events import digest
    from naiyue_test_agent.contracts.cloud_runner import MCPRefreshDispatch
    from test_mcp_connections import facts

    async def scenario():
        root, state = directories(tmp_path)
        audit = tmp_path / 'mcp.jsonl'
        model = StreamingToolModel(replies=[call('mcp__logs__echo', {'trace_id': 'new'}), AIMessage(content='done')])
        async with http_server(audit, 'stateful', json_response=True) as url:
            settings = CloudMCPSettings.model_validate({'servers': {'logs': {'url': url}}})
            b = bootstrap()
            async with open_runner(b, root, state, catalog=ModelCatalog('A', {'A': model})) as kernel:
                transport = RunnerClient(kernel)
                reports = []

                async def send(value):
                    reports.append(value)

                transport.send = send
                frame = MCPRefreshDispatch(kind='mcp_refresh', refresh_id=str(uuid4()), settings=settings,
                    expires_at=time.time() + 20, digest=digest(dict(settings=settings.model_dump(), services=None)),
                    generation=b.generation, boot_id=b.boot_id, state_epoch=b.state_epoch, connection_id=str(uuid4()))
                await transport.apply_mcp(frame)
                assert reports[-1]['refresh_id'] == frame.refresh_id
                discovery_calls = len([item for item in facts(audit) if item['event'] == 'list'])
                audit.with_suffix('.schema-v2').touch()
                await transport.apply_mcp(frame.model_copy(update={'connection_id': str(uuid4())}))
                assert reports[-1] == reports[0]
                assert len([item for item in facts(audit) if item['event'] == 'list']) == discovery_calls
                refreshed = frame.model_copy(update={'refresh_id': str(uuid4())})
                await transport.apply_mcp(refreshed)
                assert reports[-1]['refresh_id'] == refreshed.refresh_id
                assert (await kernel.execute(request(mcp_revision=refreshed.refresh_id)))['status'] == 'completed'
                assert [row['arguments'] for row in calls(audit)] == [{'trace_id': 'new'}]
    asyncio.run(scenario())


def test_blocked_service_does_not_delay_other_catalog_discovery(tmp_path):
    from test_mcp_connections import facts, until

    async def scenario():
        root, state = directories(tmp_path)
        blocked, healthy = tmp_path / 'blocked.jsonl', tmp_path / 'healthy.jsonl'
        async with http_server(blocked, 'blocked_until_release', json_response=True) as blocked_url, http_server(healthy, json_response=True) as healthy_url:
            settings = CloudMCPSettings.model_validate({'servers': {
                'blocked': {'url': blocked_url}, 'healthy': {'url': healthy_url},
            }})
            async with open_runner(bootstrap(), root, state,
                    catalog=ModelCatalog('A', {'A': StreamingToolModel(replies=[])})) as kernel:
                refresh = asyncio.create_task(kernel.mcp.apply(settings, None))
                try:
                    async with asyncio.timeout(4):
                        await until(lambda: any(item['event'] == 'list' for item in facts(healthy)))
                    assert not refresh.done()
                finally:
                    blocked.with_suffix('.release-catalog').touch()
                    await refresh
    asyncio.run(scenario())


def test_old_runner_is_not_sent_startup_mcp_configuration(tmp_path):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from cloud_helpers import owner_headers
    from lifecycle_helpers import config
    from test_runner_control import client, connect, hello, prepared

    settings = CloudMCPSettings.model_validate({'servers': {'logs': {'url': 'https://logs.example.test/mcp'}}})
    with open_cloud_runtime(config(tmp_path).model_copy(update={'mcp': settings})) as runtime:
        b = prepared(runtime)
        with client(runtime) as http, connect(http, runtime, b) as ws:
            ws.send_json(hello(b))
            welcome = ws.receive_json()
            fence = {key: welcome[key] for key in ('connection_id', 'generation', 'boot_id', 'state_epoch')}
            assert welcome == {'kind': 'welcome'} | fence
            path = f'/api/cloud/v1/tasks/{b.task_id}/mcp'
            headers = owner_headers(runtime)
            assert http.get(path, headers=headers).json()['status'] == 'unsupported'
            response = http.post(path, headers=headers, json={'refresh_id': str(uuid4())})
            assert response.status_code == 409
            assert response.json()['error']['code'] == 'cloud_mcp_unsupported'
            ws.send_json(dict(kind='heartbeat', **fence))
            assert ws.receive_json() == welcome


def test_mcp_close_failure_preserves_runner_error_and_closes_remote_models(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    from naiyue_test_agent.cloud_runner.kernel import RunnerKernel
    from naiyue_test_agent.cloud_runner import models

    async def scenario():
        root, state = directories(tmp_path)
        primary = RuntimeError('runner assembly failed')
        closed = []

        async def close_models():
            closed.append('models')

        async def close_mcp():
            closed.append('mcp')
            raise RuntimeError('mcp cleanup failed')

        async def fail_assembly(kernel):
            kernel.mcp = SimpleNamespace(resources=SimpleNamespace(aclose=close_mcp))
            raise primary

        monkeypatch.setattr(models, 'RunnerModels', lambda _: SimpleNamespace(catalog=object(), aclose=close_models))
        monkeypatch.setattr(RunnerKernel, 'assemble', fail_assembly)
        b = bootstrap().model_copy(update={'models': object()})
        with pytest.raises(RuntimeError) as error:
            async with open_runner(b, root, state):
                pytest.fail('assembly should have failed')
        assert error.value is primary
        assert closed == ['mcp', 'models']

    asyncio.run(scenario())


def test_legacy_runner_skips_mcp_dispatch_work(tmp_path, monkeypatch):
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from lifecycle_helpers import config
    from test_runner_control import client, connect, hello, prepared

    with open_cloud_runtime(config(tmp_path)) as runtime:
        b = prepared(runtime)
        checked = []
        monkeypatch.setattr(runtime.mcp, 'prepare', lambda connection: checked.append(connection.task_id))
        with client(runtime) as http, connect(http, runtime, b) as ws:
            ws.send_json(hello(b))
            welcome = ws.receive_json()
            fence = {key: welcome[key] for key in ('connection_id', 'generation', 'boot_id', 'state_epoch')}
            ws.send_json(dict(kind='heartbeat', **fence))
            assert ws.receive_json() == welcome
        assert checked == []


def test_cancelled_control_request_still_marks_runner_disconnected(tmp_path):
    from anyio import CancelScope
    from naiyue_test_agent.application.cloud.runner_control import Connection
    from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
    from naiyue_test_agent.contracts.cloud_runner import Hello
    from lifecycle_helpers import config
    from test_runner_control import hello, prepared

    async def scenario(runtime, b):
        value = Hello.model_validate(hello(b))
        connection = Connection(runtime.control, None, b.token.get_secret_value(), value)
        runtime.control.bind(connection, value)
        runtime.control.connections[b.task_id] = connection
        with CancelScope() as cancel:
            cancel.cancel()
            await runtime.control.detach(connection)
        task = runtime.registry.get_task(b.task_id)
        assert task.runner_connection_id is None
        assert task.runner_phase == 'disconnected'

    with open_cloud_runtime(config(tmp_path)) as runtime:
        asyncio.run(scenario(runtime, prepared(runtime)))
