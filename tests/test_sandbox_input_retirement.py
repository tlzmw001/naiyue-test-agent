"""Explicit input retirement releases only data with no execution/history owner."""
import asyncio
from contextlib import ExitStack
from threading import Event
from uuid import uuid4

import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder


class PreparationInterrupted(BaseException):
    pass


@pytest.mark.parametrize('interrupted', [False, True])
def test_deleted_session_returns_input_capacity_after_reopen(tmp_path, monkeypatch, interrupted):
    root = repository(tmp_path)
    configuration = dict(catalog(), git=dict(max_snapshot_records=1))

    def stop(builder, *args, **kwargs):
        builder.directory.mkdir(parents=True)
        (builder.directory/'partial').write_bytes(b'incomplete')
        raise PreparationInterrupted()

    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, configuration) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                with monkeypatch.context() as patch:
                    if interrupted:
                        patch.setattr(SnapshotBuilder, 'prepare', stop)
                        with pytest.raises(PreparationInterrupted):
                            await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                        sid = (await host.sessions.list())[0]['session_id']
                    else:
                        sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                        await host.sessions.environment_action(sid, 'destroy',confirm_destroy=True)
                execution = (await host.sessions.status(sid))['execution']
                ident = execution['input_id']
                directory = app.environments._inputs.directory(project.project_id, ident)
                assert directory.exists()
            async with application(tmp_path, configuration) as app:
                host = await app.projects.open(project.project_id)
                await host.sessions.delete(sid)
                with pytest.raises(GitInputError, match='git_input_not_found'):
                    await app.environments.input(project.project_id, ident)
                assert not directory.exists()
                next_sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                assert (await host.sessions.status(next_sid))['execution']['state'] == 'ready'
    asyncio.run(scenario())


def test_explicit_retirement_preserves_live_session_and_environment(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                ident = (await host.sessions.status(sid))['execution']['input_id']
                assert await app.environments.retire_input(project.project_id, ident) is False
                await host.sessions.environment_action(sid, 'destroy',confirm_destroy=True)
                assert await app.environments.retire_input(project.project_id, ident) is False
                assert (await app.environments.input(project.project_id, ident)).state == 'ready'
    asyncio.run(scenario())


def test_explicit_retirement_waits_for_prepare_and_can_reclaim_orphan(tmp_path, monkeypatch):
    root = repository(tmp_path)
    entered, release, retiring = Event(), Event(), Event()
    original = SnapshotBuilder.prepare

    def prepare(builder, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(builder, *args, **kwargs)

    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                monkeypatch.setattr(SnapshotBuilder, 'prepare', prepare)
                retire = app.environments._inputs.retire
                def observed_retire(*args):
                    retiring.set()
                    return retire(*args)
                monkeypatch.setattr(app.environments._inputs, 'retire', observed_retire)
                ident = 'a'*32
                task = asyncio.create_task(app.environments.prepare_input(project.project_id, input_id=ident))
                retirement = None
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    retirement = asyncio.create_task(app.environments.retire_input(project.project_id, ident))
                    assert await asyncio.to_thread(retiring.wait, 10)
                    assert not retirement.done()
                finally:
                    release.set()
                    ready = await task
                assert ready.state == 'ready'
                assert await retirement is True
                assert await app.environments.retire_input(project.project_id, ident) is False
                with pytest.raises(GitInputError, match='git_input_not_found'):
                    await app.environments.input(project.project_id, ident)
    asyncio.run(scenario())


def test_result_review_and_delivery_still_work_after_session_deletion(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                execution = (await host.sessions.status(sid))['execution']
                binding, ident = execution['binding_id'], execution['input_id']
                (tmp_path/binding/'app.txt').write_text('retained result\n')
                await host.sessions.capture_result(sid, result_id='c'*32, include_untracked=[])
                await host.sessions.environment_action(sid, 'destroy',confirm_destroy=True)
                await host.sessions.delete(sid)
                assert await app.environments.retire_input(project.project_id, ident) is False
                assert '+retained result' in (await app.environments.result_diff(binding, 'c'*32, 'app.txt'))['patch']
                delivered = await app.environments.deliver(binding, 'c'*32,
                    dict(operation_id='d'*32, action='branch', branch='retirement-review'))
                assert delivered['state'] == 'completed'
                assert await app.environments.retire_input(project.project_id, ident) is False
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['result', 'receipt', 'delivery'])
def test_registry_references_protect_input_without_a_session(tmp_path, monkeypatch, kind):
    from naiyue_test_agent.contracts.git_delivery import DeliveryCommand
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                execution = (await host.sessions.status(sid))['execution']
                binding, ident = execution['binding_id'], execution['input_id']
                if kind == 'delivery':
                    app.environments._delivery.records().reserve(project.project_id, binding, 'c'*32,
                        DeliveryCommand(operation_id='d'*32, action='branch', branch='review'),
                        app.environments._delivery.settings)
                else:
                    metadata = dict(delivery=dict(receipt=dict(input_id=ident))) if kind == 'receipt' else {}
                    if kind == 'receipt':
                        # A retained mismatched receipt must also prevent data deletion.
                        row = await app.environments.get(binding)
                        app.environments._service.records().reserve(row.changed(binding_id='another',
                            session_id='another',lifecycle='destroyed',input_id=None))
                    app.environments._operations.records().reserve('another' if kind == 'receipt' else binding,
                        project.project_id, sid, 'c'*32, 'git_result', {}, metadata)
                await host.sessions.environment_action(sid, 'destroy',confirm_destroy=True)
                await host.sessions.delete(sid)
                assert await app.environments.retire_input(project.project_id, ident) is False
                assert (await app.environments.input(project.project_id, ident)).state == 'ready'
                assert app.environments._inputs.directory(project.project_id, ident).exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('state', ['ready', 'preparing', 'failed'])
def test_cold_orphan_can_be_retired_explicitly_without_loading_models(tmp_path, monkeypatch, state):
    from dataclasses import replace
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                other_root = tmp_path/'other';other_root.mkdir()
                other = await app.projects.add_local(other_root, trusted=True, operation_id='other')
                if state == 'ready':
                    await app.environments.prepare_input(project.project_id, input_id='a'*32)
                else:
                    inputs = app.environments._inputs
                    row, _ = inputs.records().reserve(project.project_id, project.revision, 'a'*32, (), inputs.environments.settings.git)
                    if state == 'failed':inputs.records().save(replace(row, state='failed', error_code='git_input_io_failed'))
                    directory = inputs.directory(project.project_id, 'a'*32)
                    directory.mkdir(parents=True)
                    (directory/'partial').write_bytes(b'partial')
            async with application(tmp_path, catalog()) as app:
                assert await app.environments.retire_input(other.project_id, 'a'*32) is False
                assert await app.environments.retire_input(project.project_id, 'a'*32) is True
                assert app._lifetime._catalog.value is None
                assert not app.environments._inputs.directory(project.project_id, 'a'*32).exists()
    asyncio.run(scenario())


def test_retirement_failure_is_observable_and_explicit_retry_keeps_identity(tmp_path, monkeypatch, caplog):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                ready = await app.environments.prepare_input(project.project_id, input_id='a'*32)
                def fail(*args):raise OSError('synthetic directory failure')
                with monkeypatch.context() as patch:
                    patch.setattr(app.environments._inputs, '_remove_directory', fail)
                    with pytest.raises(OSError, match='synthetic directory failure'):
                        await app.environments.retire_input(project.project_id, 'a'*32)
                assert await app.environments.input(project.project_id, 'a'*32) == ready
                assert await app.environments.retire_input(project.project_id, 'a'*32) is True
    asyncio.run(scenario())
    assert 'git_input_retirement_failed' in caplog.text


def test_install_reserves_input_reference_before_releasing_input_lock(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create('fake')
                env = await app.environments.create(project.project_id, sid)
                await app.environments.prepare_input(project.project_id, input_id='a'*32)
                inputs = app.environments._inputs
                entered, release, retiring = Event(), Event(), Event()
                get, retire = inputs.get, inputs.retire
                def reading(*args):
                    row = get(*args)
                    entered.set()
                    assert release.wait(10)
                    return row
                def retiring_input(*args):
                    retiring.set()
                    return retire(*args)
                monkeypatch.setattr(inputs, 'get', reading)
                monkeypatch.setattr(inputs, 'retire', retiring_input)
                task = asyncio.create_task(app.environments.install_input(env.binding_id, 'a'*32))
                retirement = None
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    retirement = asyncio.create_task(app.environments.retire_input(project.project_id, 'a'*32))
                    assert await asyncio.to_thread(retiring.wait, 10)
                    assert not retirement.done()
                finally:
                    release.set()
                    installed = await task
                assert installed.input_id == 'a'*32
                assert await retirement is False
    asyncio.run(scenario())


def test_retirement_and_new_session_owner_cannot_cross(tmp_path, monkeypatch):
    from uuid import uuid5, NAMESPACE_URL
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                request = str(uuid4())
                ident = uuid5(NAMESPACE_URL, 'nyt:input:'+project.project_id+':'+request).hex
                await app.environments.prepare_input(project.project_id, input_id=ident)
                inputs = app.environments._inputs
                removing, release, owner_waiting = Event(), Event(), Event()
                remove, lock = inputs._remove_directory, inputs._lock
                calls = []
                def observed_lock(key):
                    if key == ident:
                        calls.append(key)
                        if len(calls) == 2:owner_waiting.set()
                    return lock(key)
                def removing_directory(*args):
                    removing.set()
                    assert release.wait(10)
                    return remove(*args)
                monkeypatch.setattr(inputs, '_lock', observed_lock)
                monkeypatch.setattr(inputs, '_remove_directory', removing_directory)
                retirement = asyncio.create_task(app.environments.retire_input(project.project_id, ident))
                creating = None
                try:
                    assert await asyncio.to_thread(removing.wait, 10)
                    creating = asyncio.create_task(host.sessions.create_sandbox('fake', request_id=request))
                    assert await asyncio.to_thread(owner_waiting.wait, 10)
                    assert await host.sessions.list() == []
                finally:
                    release.set()
                    assert await retirement is True
                    if creating is not None:sid = await creating
                assert (await host.sessions.status(sid))['execution']['state'] == 'ready'
                assert (await app.environments.input(project.project_id, ident)).state == 'ready'
    asyncio.run(scenario())


def test_session_delete_retirement_blocks_host_close_until_settled(tmp_path, monkeypatch):
    from naiyue_test_agent.contracts.projects import ProjectError
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                await host.sessions.environment_action(sid, 'destroy',confirm_destroy=True)
                retiring, release = asyncio.Event(), asyncio.Event()
                retire = app.environments.retire_input
                async def paused_retire(*args):
                    retiring.set()
                    await release.wait()
                    return await retire(*args)
                monkeypatch.setattr(app.environments, 'retire_input', paused_retire)
                deletion = asyncio.create_task(host.sessions.delete(sid))
                await asyncio.wait_for(retiring.wait(), 10)
                try:
                    with pytest.raises(ProjectError, match='project_in_use'):
                        await app.projects.close(project.project_id)
                finally:release.set()
                await deletion
                await app.projects.close(project.project_id)
                assert host._lifetime._closed
    asyncio.run(scenario())


def test_destroyed_capture_cannot_register_new_result_after_input_retirement(tmp_path, monkeypatch):
    from naiyue_test_agent.contracts.sandbox import SandboxError
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                execution = (await host.sessions.status(sid))['execution']
                binding, ident = execution['binding_id'], execution['input_id']
                await host.sessions.environment_action(sid, 'destroy',confirm_destroy=True)
                entered, release = Event(), Event()
                get = app.environments._inputs.get
                def reading(*args):
                    record = get(*args)
                    entered.set()
                    assert release.wait(10)
                    return record
                monkeypatch.setattr(app.environments._inputs, 'get', reading)
                capture = asyncio.create_task(app.environments.capture_result(binding, result_id='c'*32, include_untracked=[]))
                try:
                    assert await asyncio.to_thread(entered.wait, 10)
                    await host.sessions.delete(sid)
                    with pytest.raises(GitInputError, match='git_input_not_found'):
                        get(project.project_id, ident)
                finally:release.set()
                with pytest.raises(SandboxError, match='env_expired'):
                    await capture
                assert app.environments._operations.records().result_rows(binding) == []
    asyncio.run(scenario())


def test_explicit_retirement_refuses_linked_directory(tmp_path, monkeypatch, caplog):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                inputs = app.environments._inputs
                inputs.records().reserve(project.project_id, project.revision, 'a'*32, (), inputs.environments.settings.git)
                directory = inputs.directory(project.project_id, 'a'*32)
                outside = tmp_path/'outside';outside.mkdir();(outside/'keep').write_bytes(b'unchanged')
                directory.parent.mkdir(parents=True)
                directory.symlink_to(outside, target_is_directory=True)
                with pytest.raises(OSError):
                    await app.environments.retire_input(project.project_id, 'a'*32)
                assert directory.is_symlink() and (outside/'keep').read_bytes() == b'unchanged'
                assert (await app.environments.input(project.project_id, 'a'*32)).state == 'preparing'
    asyncio.run(scenario())
    assert 'git_input_retirement_failed' in caplog.text
