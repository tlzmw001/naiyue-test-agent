import asyncio
import os
from pathlib import Path
from uuid import uuid4

from naiyue_test_agent.cloud_files.cache import capture_cached
from naiyue_test_agent.contracts.cache_cleanup import PATH
from naiyue_test_agent.contracts.cloud_inputs import InputLimits, Selection
from naiyue_test_agent.tools.output import _directory
from test_async_application import StreamingToolModel
from test_service_protocol import service_clients


def test_authenticated_cleanup_and_receipt_cover_cold_registered_project(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, _, app, _):
            source = tmp_path / 'project'
            source.mkdir()
            (source / 'source.txt').write_text('source')
            project = await app.state.application.projects.add_local(
                str(source), trusted=True, operation_id=str(uuid4()))
            local = project.database_path.parent
            archive = _directory(local / 'artifacts', create=True) / (uuid4().hex + '-raw.log')
            archive.write_text('old')
            capture = capture_cached(source, Selection(paths=['source.txt']),
                                     local / 'cloud-input-cache', InputLimits())
            for file in [archive, *capture.directory.iterdir()]:
                os.utime(file, (1, 1))
            instance = {'X-Service-Instance': app.state.owner.record.instance_id}
            assert (await client.get(PATH)).status_code == 409
            response = await client.get(PATH, headers=instance)
            assert response.status_code == 200
            assert [item['available'] for item in response.json()['categories']] == [True, True]
            rid = str(uuid4())
            headers = {**instance, 'X-Request-ID': rid}
            body = {'request_id': rid, 'categories': ['tool_outputs', 'cloud_inputs']}
            response = await client.post(PATH, headers=headers, json=body)
            assert response.status_code == 200
            result = response.json()
            assert result['status'] == 'completed', result
            assert result['totals']['units_deleted'] == 2
            assert not archive.exists() and not capture.directory.exists()
            assert (source / 'source.txt').read_text() == 'source'
            assert not app.state.application._lifetime._children
            receipt = await client.get(f'{PATH}/{rid}', headers=instance)
            assert receipt.status_code == 200
            assert receipt.json()['result'] == result
            assert (await client.post(PATH, headers=headers, json=body)).json() == result
            invalid = await client.post(PATH, headers={'X-Request-ID': rid}, json=body)
            assert invalid.status_code == 409
    asyncio.run(scenario())


def test_upload_reader_blocks_cleanup_and_interrupted_retirement_can_resume(monkeypatch, tmp_path):
    from naiyue_test_agent.cloud_files.cache_access import package_reader
    from naiyue_test_agent.storage.cache_cleanup_files import Directory

    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, _, app, _):
            source = tmp_path / 'project'
            source.mkdir()
            (source / 'data').write_text('preserve')
            project = await app.state.application.projects.add_local(
                str(source), trusted=True, operation_id=str(uuid4()))
            cache = project.database_path.parent / 'cloud-input-cache'
            captured = capture_cached(source, Selection(paths=['data']), cache, InputLimits())
            for file in captured.directory.iterdir():
                os.utime(file, (1, 1))
            (captured.directory / 'upload.lock').touch()

            async def clear():
                rid = str(uuid4())
                headers = {'X-Service-Instance': app.state.owner.record.instance_id, 'X-Request-ID': rid}
                response = await client.post(PATH, headers=headers,
                    json={'request_id': rid, 'categories': ['cloud_inputs']})
                assert response.status_code == 200
                return response.json()

            with package_reader(captured.directory):
                assert (await clear())['details'][0]['code'] == 'input_cache_busy'
                assert captured.directory.is_dir()

            unlink = Directory.unlink

            def interrupt(self, name, expected):
                if self.path.name == 'payload' and name == 'receipt.json':
                    raise PermissionError('injected deletion failure')
                return unlink(self, name, expected)

            with monkeypatch.context() as patch:
                patch.setattr(Directory, 'unlink', interrupt)
                result = await clear()
                assert result['status'] == 'partial'
                detail = result['details'][0]
                assert detail['code'] == 'permission_denied'
                assert detail['step'] == 'unlink'
                assert cache in Path(detail['path']).parents
                assert Path(detail['path']).is_file()
                assert Path(detail['path']).parts[-2:] == ('payload', 'receipt.json')
                assert result['totals']['files_deleted'] > 0
            assert not captured.directory.exists()
            new = capture_cached(source, Selection(paths=['data']), cache, InputLimits())
            result = await clear()
            assert result['status'] == 'completed', result
            assert not list((cache / 'staging').iterdir())
            assert new.directory.is_dir()
    asyncio.run(scenario())


def test_cleanup_keeps_inflight_receipt_after_caller_disconnect(monkeypatch, tmp_path):
    import pytest

    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, other, app, _):
            cleanup = app.state.application.cache_cleanup
            from naiyue_test_agent.application.projects import cache_cleanup_operation as module
            entered, release = asyncio.Event(), asyncio.Event()
            original = module.scan

            async def pause(hosts, run):
                entered.set()
                await release.wait()
                await original(hosts, run)

            monkeypatch.setattr(module, 'scan', pause)
            instance = {'X-Service-Instance': app.state.owner.record.instance_id}
            rid = str(uuid4())
            body = {'request_id': rid, 'categories': ['tool_outputs']}
            headers = {**instance, 'X-Request-ID': rid}
            pending = asyncio.create_task(client.post(PATH, headers=headers, json=body))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                status = await other.get(f'{PATH}/{rid}', headers=instance)
                assert status.json()['state'] == 'running' and cleanup.running
                second = str(uuid4())
                rejected = await other.post(PATH, headers={**instance, 'X-Request-ID': second},
                    json={'request_id': second, 'categories': ['tool_outputs']})
                assert rejected.status_code == 409
                assert rejected.json()['error']['code'] == 'cache_cleanup_busy'
            finally:
                release.set()
            result = await other.post(PATH, headers=headers, json=body)
            assert result.status_code == 200
            status = await other.get(f'{PATH}/{rid}', headers=instance)
            assert status.json()['result'] == result.json()
    asyncio.run(scenario())


def test_too_small_frame_rejects_before_scanning_and_receipt_fits(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[]),
                                   max_frame_bytes=1024) as (client, _, app, _):
            rid = str(uuid4())
            headers = {'X-Service-Instance': app.state.owner.record.instance_id, 'X-Request-ID': rid}
            response = await client.post(PATH, headers=headers,
                json={'request_id': rid, 'categories': ['tool_outputs', 'cloud_inputs']})
            assert response.status_code == 503
            assert response.json()['error']['code'] == 'cache_cleanup_result_budget'
            receipt = await client.get(f'{PATH}/{rid}', headers=headers)
            assert receipt.json()['http_status'] == 503
            assert len(receipt.content) <= 1024
    asyncio.run(scenario())


def test_archive_preserves_failed_session_and_reports_missing_chat_store(monkeypatch, tmp_path):
    import sqlite3

    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, _, app, _):
            application = app.state.application
            source = tmp_path / 'project'
            source.mkdir()
            project = await application.projects.add_local(str(source), trusted=True, operation_id=str(uuid4()))
            hosts = application.cache_cleanup.hosts
            host = await hosts.open(project.project_id)
            await host.sessions.create(model_id='A')
            await hosts.close(project.project_id)
            with sqlite3.connect(project.database_path) as connection:
                connection.execute("UPDATE naiyue_sessions SET state='failed'")
            archive = _directory(project.database_path.parent / 'artifacts', create=True) / (uuid4().hex + '-raw.log')
            archive.touch()
            os.utime(archive, (1, 1))
            orphan = _directory(hosts.projects.data_dir / 'sessions' / str(uuid4()) / 'artifacts', create=True)
            async def cleanup():
                rid = str(uuid4())
                headers = {'X-Service-Instance': app.state.owner.record.instance_id, 'X-Request-ID': rid}
                response = await client.post(PATH, headers=headers, json={'request_id': rid, 'categories': ['tool_outputs']})
                assert response.status_code == 200
                return response.json()

            from naiyue_test_agent.storage.project_databases import locked_project_reader
            with locked_project_reader(project.database_path):
                result = await cleanup()
                assert {d['code'] for d in result['details']} == {'scope_busy', 'metadata_unavailable'}
            result = await cleanup()
            assert result['status'] == 'partial'
            assert {d['code'] for d in result['details']} == {'recovery_required', 'metadata_unavailable'}
            assert archive.exists() and orphan.exists()
    asyncio.run(scenario())


def test_valid_combined_metadata_and_bounded_retirement(tmp_path):
    from naiyue_test_agent.application.projects.cache_cleanup_operation import CleanupRun, StopCleanup
    from naiyue_test_agent.cache_cleanup_settings import CacheCleanupSettings
    from naiyue_test_agent.cloud_files.cache_cleanup import package_files
    from naiyue_test_agent.cloud_files.cache_retirement import retire
    from naiyue_test_agent.storage.cache_cleanup_files import identity, pinned_directory
    import pytest

    source = tmp_path / 'src'
    source.mkdir()
    names = ['f' + str(i) for i in range(10)]
    for name in names:
        (source / name).touch()
    cache = tmp_path / 'cache'
    limits = InputLimits(max_metadata_bytes=1750)
    captured = capture_cached(source, Selection(paths=names), cache, limits)
    assert (captured.directory / 'input.json').stat().st_size > limits.max_metadata_bytes
    run = CleanupRun(str(uuid4()), ['cloud_inputs'], CacheCleanupSettings(), 65536)
    with pinned_directory(captured.directory) as directory:
        files = package_files(directory, cache, captured.input_id, run, limits.max_metadata_bytes)
    with pinned_directory(cache / 'inputs') as inputs, pinned_directory(cache / 'staging') as staging:
        run.remaining = len(files) + 1
        with pytest.raises(StopCleanup):
            retire(inputs, staging, captured.input_id, identity(captured.directory.stat())[:2],
                   files, run, limits.max_metadata_bytes)
    assert captured.directory.exists()
    assert not list((cache / 'staging').glob('*/payload'))


def test_corrupt_source_receipt_is_rejected_before_preparation(tmp_path):
    from types import SimpleNamespace
    import pytest
    from naiyue_test_agent.application.cloud_local.tasks import LocalTaskDomain
    from naiyue_test_agent.contracts.cloud_tasks import CloudError

    source = tmp_path / 'src'
    source.mkdir()
    (source / 'file').touch()
    cache = tmp_path / 'cloud-input-cache'
    captured = capture_cached(source, Selection(paths=['file']), cache, InputLimits())
    domain = SimpleNamespace(cache=cache,
        repo=SimpleNamespace(source=lambda *_: {'captured': captured.model_dump(mode='json')}))
    asyncio.run(LocalTaskDomain.check_source_cache(domain, 'handle', 'session'))
    (captured.directory / 'receipt.json').unlink()
    with pytest.raises(CloudError, match='input_cache_invalid'):
        asyncio.run(LocalTaskDomain.check_source_cache(domain, 'handle', 'session'))


def test_cleanup_validates_canonical_request_id_before_receipt_reuse(monkeypatch, tmp_path):
    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, _, app, _):
            instance = app.state.owner.record.instance_id
            for order in ((True, False), (False, True)):
                rid = 'abcdef00-' + str(uuid4())[9:]
                for canonical in order:
                    response = await client.post(PATH, headers={
                        'X-Service-Instance': instance, 'X-Request-ID': rid if canonical else rid.upper(),
                    }, json={'request_id': rid, 'categories': ['tool_outputs']})
                    assert response.status_code == (200 if canonical else 422)
    asyncio.run(scenario())


def test_cleanup_stops_at_next_unit_when_service_starts_closing(monkeypatch, tmp_path):
    from naiyue_test_agent.application.projects import cache_cleanup_operation as module

    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, _, app, _):
            source = tmp_path / 'project'
            source.mkdir()
            project = await app.state.application.projects.add_local(
                str(source), trusted=True, operation_id=str(uuid4()))
            log = _directory(project.database_path.parent / 'artifacts', create=True) / (uuid4().hex + '-raw.log')
            log.touch()
            os.utime(log, (1, 1))
            entered, proceed, draining, drained = (asyncio.Event() for _ in range(4))
            scan = module.scan
            close = app.state.scopes.cloud_tasks.close

            async def pause(hosts, run):
                entered.set()
                await proceed.wait()
                await scan(hosts, run)

            async def pause_cloud_close():
                draining.set()
                await drained.wait()
                await close()

            monkeypatch.setattr(module, 'scan', pause)
            monkeypatch.setattr(app.state.scopes.cloud_tasks, 'close', pause_cloud_close)
            rid = str(uuid4())
            pending = asyncio.create_task(client.post(PATH, headers={
                'X-Service-Instance': app.state.owner.record.instance_id, 'X-Request-ID': rid,
            }, json={'request_id': rid, 'categories': ['tool_outputs']}))
            await asyncio.wait_for(entered.wait(), 5)
            shutdown = asyncio.create_task(app.state.scopes.shutdown(app.state.receipts))
            try:
                await asyncio.wait_for(draining.wait(), 5)
                proceed.set()
                response = await asyncio.wait_for(pending, 5)
                assert response.json()['stop_reason'] == 'service_closing'
                assert log.exists()
            finally:
                proceed.set()
                drained.set()
                await shutdown

    asyncio.run(scenario())


def test_cleanup_covers_registered_projects_and_saved_chats_only(monkeypatch, tmp_path):
    import hashlib
    from naiyue_test_agent.application.projects import cache_cleanup_operation

    async def scenario():
        async with service_clients(monkeypatch, tmp_path, StreamingToolModel(replies=[])) as (client, _, app, _):
            application = app.state.application
            expected_deleted, expected_preserved = [], []
            hosts = application.cache_cleanup.hosts
            cutoff = 1_700_000_000_000_000_000

            def log(folder, stamp):
                artifact = _directory(folder, create=True) / (uuid4().hex + '-raw.log')
                artifact.write_bytes(b'output fixture')
                os.utime(artifact, ns=(stamp, stamp))
                return artifact

            for name in ('open', 'cold', 'removed'):
                source = tmp_path / name
                source.mkdir()
                project = await application.projects.add_local(str(source), trusted=True, operation_id=str(uuid4()))
                folder = project.database_path.parent / 'artifacts'
                if name == 'open':
                    await hosts.open(project.project_id)
                if name == 'removed':
                    await application.projects.remove(project.project_id, operation_id=str(uuid4()))
                    expected_preserved.append(log(folder, cutoff - 1_000_000_000))
                else:
                    expected_deleted.append(log(folder, cutoff - 1_000_000_000))
                    expected_preserved.append(log(folder, cutoff))
                    expected_preserved.append(log(folder, cutoff + 1_000_000_000))
            chats = await hosts.open(None)
            chat_ids = [await chats.sessions.create('A') for _ in range(2)]
            await hosts.close(None)
            for session_id in chat_ids:
                expected_deleted.append(log(hosts.projects.data_dir / 'sessions' / session_id / 'artifacts',
                                            cutoff - 1_000_000_000))
            expected_preserved.append(log(hosts.projects.data_dir / 'sessions' / str(uuid4()) / 'artifacts',
                                          cutoff - 1_000_000_000))
            before = [hashlib.sha256(path.read_bytes()).digest() for path in expected_preserved]
            with monkeypatch.context() as clock:
                clock.setattr(cache_cleanup_operation.time, 'time_ns', lambda: cutoff + 7 * 86400 * 10**9)
                rid = str(uuid4())
                response = await client.post(PATH, headers={
                    'X-Service-Instance': app.state.owner.record.instance_id, 'X-Request-ID': rid,
                }, json={'request_id': rid, 'categories': ['tool_outputs']})
            result = response.json()
            assert result['status'] == 'completed', str(result['details'])
            assert result['totals']['files_deleted'] == len(expected_deleted)
            assert all(not path.exists() for path in expected_deleted)
            assert [hashlib.sha256(path.read_bytes()).digest() for path in expected_preserved] == before
            assert None not in hosts._hosts

    asyncio.run(scenario())
