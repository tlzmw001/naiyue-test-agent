"""Durable input preparation uses registered project facts without opening a model."""
import asyncio
from contextlib import closing
from dataclasses import replace
from threading import Event

import pytest

from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.application.environments.inputs import InputService
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.sandbox_settings import GitInputSettings
from naiyue_test_agent.storage.git_inputs import GitInputs
from naiyue_test_agent.storage.project_registry import ProjectRegistry
from git_input_helpers import git, repository


def test_public_api_is_lazy_idempotent_and_reopen_keeps_fixed_snapshot(tmp_path):
    root=repository(tmp_path)
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    async def scenario():
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            project=await app.projects.add_local(root,trusted=True,operation_id='P')
            (root/'app.txt').write_text('first\n')
            first=await app.environments.prepare_input(project.project_id,input_id='a'*32)
            assert first.state=='ready' and first.snapshot.input_snapshot!=first.snapshot.original_head
            (root/'app.txt').write_text('later\n')
            again=await app.environments.prepare_input(project.project_id,input_id='a'*32)
            assert again==first
            assert app._lifetime._catalog.value is None
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            loaded=await app.environments.input(project.project_id,'a'*32)
            assert loaded==first
            with pytest.raises(GitInputError,match='git_input_conflict'):
                await app.environments.prepare_input(project.project_id,input_id='a'*32,include_untracked=('other.txt',))
    asyncio.run(scenario())


def test_record_failure_and_incomplete_preparation_are_not_silently_retried(tmp_path):
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    root=repository(tmp_path)
    async def scenario():
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            project=await app.projects.add_local(root,trusted=True,operation_id='P')
            records=app.environments._inputs.records()
            row,new=records.reserve(project.project_id,project.revision,'a'*32,(),settings.sandbox.git)
            assert new and row.state=='preparing'
            with pytest.raises(GitInputError,match='git_input_unconfirmed'):
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
            (root/'.env').write_text('FIXTURE=not-a-secret\n');git(root,'add','--force','.env')
            with pytest.raises(GitInputError,match='git_input_sensitive'):
                await app.environments.prepare_input(project.project_id,input_id='b'*32)
            failed=await app.environments.input(project.project_id,'b'*32)
            assert failed.state=='failed' and failed.error_code=='git_input_sensitive'
            assert failed.error_paths==('.env',)
            with pytest.raises(GitInputError,match='git_input_sensitive'):
                await app.environments.prepare_input(project.project_id,input_id='b'*32)
    asyncio.run(scenario())


def test_failed_snapshot_cleans_partial_cache_and_new_id_can_retry(tmp_path):
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    root=repository(tmp_path)
    async def scenario():
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            project=await app.projects.add_local(root,trusted=True,operation_id='P')
            inputs=app.environments._inputs
            with pytest.raises(GitInputError,match='git_input_budget'):
                await app.environments.prepare_input(project.project_id,input_id='a'*32,
                    git_settings=GitInputSettings(max_file_bytes=2))
            assert not inputs.directory(project.project_id,'a'*32).exists()
            with pytest.raises(GitInputError,match='git_input_budget'):
                await app.environments.prepare_input(project.project_id,input_id='a'*32,
                    git_settings=GitInputSettings())
            ready=await app.environments.prepare_input(project.project_id,input_id='b'*32)
            assert ready.state=='ready' and ready.snapshot is not None
            assert inputs.get(project.project_id,'b'*32)==ready
            assert await app.environments.retire_input(project.project_id,'b'*32) is True
            with pytest.raises(GitInputError,match='git_input_not_found'):
                inputs.get(project.project_id,'b'*32)
    asyncio.run(scenario())


def test_failed_snapshot_cleanup_refuses_symlink_and_preserves_source(tmp_path, caplog):
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    root=repository(tmp_path)
    outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep')
    async def scenario():
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            project=await app.projects.add_local(root,trusted=True,operation_id='P')
            inputs=app.environments._inputs
            directory=inputs.directory(project.project_id,'a'*32)
            directory.parent.mkdir(parents=True)
            directory.symlink_to(outside,target_is_directory=True)
            with pytest.raises(GitInputError,match='git_input_io_failed'):
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
            assert directory.is_symlink() and (outside/'keep').read_text()=='keep'
            assert inputs.get(project.project_id,'a'*32).state=='failed'
    asyncio.run(scenario())
    assert 'git_input_partial_cleanup_failed' in caplog.text


def test_revision_change_before_ready_and_required_save_failure_refuse_success(tmp_path,monkeypatch):
    root=repository(tmp_path)
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
    prepare=SnapshotBuilder.prepare
    async def scenario():
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            project=await app.projects.add_local(root,trusted=True,operation_id='P')
            def moved(builder,*args,**kwargs):
                result=prepare(builder,*args,**kwargs)
                app.environments._service.projects.rename(project.project_id,'renamed',operation_id='rename')
                return result
            monkeypatch.setattr(SnapshotBuilder,'prepare',moved)
            with pytest.raises(GitInputError,match='git_input_source_changed'):
                await app.environments.prepare_input(project.project_id,input_id='a'*32)
            monkeypatch.setattr(SnapshotBuilder,'prepare',prepare)
            save=GitInputs.save
            def reject_ready(repo,row):
                if row.state=='ready':raise OSError('synthetic storage failure')
                return save(repo,row)
            monkeypatch.setattr(GitInputs,'save',reject_ready)
            with pytest.raises(GitInputError,match='git_input_io_failed'):
                await app.environments.prepare_input(project.project_id,input_id='b'*32)
            assert (await app.environments.input(project.project_id,'b'*32)).state!='ready'
    asyncio.run(scenario())


def test_git_source_preparation_does_not_open_host_or_grant_project_trust(tmp_path):
    root=repository(tmp_path)
    settings=AppSettings.model_validate({'storage':{'data_dir':str(tmp_path/'data')}})
    async def scenario():
        async with open_service_application('missing-model',tools=(),system_prompt='',response_format=None,settings=settings) as app:
            project=await app.projects.add_git(root.as_uri(),ref='HEAD',name='remote',operation_id='G')
            row=await app.environments.prepare_input(project.project_id,input_id='a'*32)
            assert row.state=='ready' and row.snapshot.original_head==git(root,'rev-parse','HEAD').decode().strip()
            assert (await app.projects.get(project.project_id)).trusted is False
            assert app._lifetime._catalog.value is None
    asyncio.run(scenario())


def test_registry_v4_migration_keeps_existing_records_and_adds_input_repository(tmp_path):
    path=tmp_path/'projects.sqlite'
    with closing(ProjectRegistry(path)) as registry:
        with registry.database.transaction(write=True) as conn:
            conn.execute('DROP TABLE git_deliveries');conn.execute('DROP TABLE git_inputs');conn.execute('PRAGMA user_version=4')
    with closing(ProjectRegistry(path)) as registry:
        assert registry.database.read_rows('PRAGMA user_version')==[{'user_version':6}]
        records=GitInputs(registry.database)
        row,new=records.reserve('P',1,'a'*32,(),AppSettings().sandbox.git)
        assert row.state=='preparing' and new
        assert records.get('P','a'*32)==row
        with pytest.raises(GitInputError,match='git_input_not_found'):records.get('another','a'*32)


@pytest.mark.parametrize('case', ['readonly', 'occupied', 'metadata_denied', 'outside', 'escaped_name'])
def test_cleanup_diagnostics_keep_original_failure_and_hide_private_paths(tmp_path, monkeypatch, caplog, case):
    import errno
    import os
    from pathlib import Path
    import stat
    from types import SimpleNamespace

    root = repository(tmp_path)
    settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path/'data')}})
    failure = PermissionError(errno.EACCES, 'PRIVATE_EXCEPTION_BODY', str(tmp_path/'PRIVATE_FILENAME'))
    failure.winerror = 32 if case in ('occupied', 'metadata_denied') else 5

    async def scenario():
        async with open_service_application('missing-model', tools=(), system_prompt='',
                response_format=None, settings=settings) as app:
            project = await app.projects.add_local(root, trusted=True, operation_id='P')
            inputs = app.environments._inputs
            ready = await app.environments.prepare_input(project.project_id, input_id='a'*32)
            directory = inputs.directory(project.project_id, ready.input_id)
            name = 'probe\nFORGED.pack' if case == 'escaped_name' else 'probe.pack'
            target = directory/'objects.git'/'objects'/'pack'/'probe.pack'
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b'owned input fixture')
            callback_path = tmp_path/'PRIVATE_OUTSIDE' if case == 'outside' else target.with_name(name)
            original_stat = Path.lstat
            attempts = []

            def inspect(path, *args, **kwargs):
                assert path != tmp_path/'PRIVATE_OUTSIDE', 'Diagnostic must not inspect paths outside the input'
                info = original_stat(target if path == callback_path else path, *args, **kwargs)
                if path != callback_path:
                    return info
                if case == 'metadata_denied':
                    error = PermissionError(errno.EACCES, 'PRIVATE_STAT_BODY', str(target))
                    error.winerror = 5
                    raise error
                return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                    st_file_attributes=stat.FILE_ATTRIBUTE_READONLY if case == 'readonly' else 0)

            def refuse(path, **kwargs):
                attempts.append(path)
                if kwargs.get('onexc') is not None:
                    kwargs['onexc'](os.unlink, str(callback_path), failure)
                raise failure

            with monkeypatch.context() as patch:
                patch.setattr(Path, 'lstat', inspect)
                patch.setattr('naiyue_test_agent.application.environments.inputs.shutil.rmtree', refuse)
                with pytest.raises(PermissionError) as caught:
                    await app.environments.retire_input(project.project_id, ready.input_id)
                assert caught.value is failure
                assert await app.environments.input(project.project_id, ready.input_id) == ready
                assert attempts == [directory]
                assert target.read_bytes() == b'owned input fixture'
                assert (root/'app.txt').read_text() == 'base\n'

            messages = [record.getMessage() for record in caplog.records
                if record.getMessage().startswith('git_input_directory_cleanup_failed ')]
            assert len(messages) == 1
            message = messages[0]
            assert f'project_id={project.project_id} input_id={ready.input_id}' in message
            assert f'operation=unlink error_type=PermissionError errno={errno.EACCES} winerror={failure.winerror}' in message
            assert 'PRIVATE_' not in caplog.text and str(tmp_path) not in caplog.text
            assert '\n' not in message
            if case == 'outside':
                assert 'relative_path="<outside_input>"' in message
                assert 'readonly=None' in message
            else:
                escaped = 'probe\\nFORGED.pack' if case == 'escaped_name' else 'probe.pack'
                assert f'relative_path="objects.git/objects/pack/{escaped}"' in message
                if case == 'metadata_denied':
                    assert 'readonly=None' in message
                    assert f'inspect_errno={errno.EACCES} inspect_winerror=5' in message
                else:
                    assert f'readonly={case == "readonly"}' in message
                    assert 'file_kind=regular reparse_point=False links=1' in message
            assert await app.environments.retire_input(project.project_id, ready.input_id) is True
            assert sum(record.getMessage().startswith('git_input_directory_cleanup_failed ')
                for record in caplog.records) == 1
    asyncio.run(scenario())


def test_real_directory_cleanup_reports_unlink_error_without_changing_it(tmp_path, monkeypatch, caplog):
    import errno
    from functools import wraps
    import os
    from pathlib import Path

    root = repository(tmp_path)
    settings = AppSettings.model_validate({'storage': {'data_dir': str(tmp_path/'data')}})
    failure = PermissionError(errno.EACCES, 'PRIVATE_EXCEPTION_BODY')
    failure.winerror = 32
    unlink = os.unlink

    @wraps(unlink)
    def occupied(path, *args, **kwargs):
        if Path(path).name == 'probe.bin':
            raise failure
        return unlink(path, *args, **kwargs)

    async def scenario():
        async with open_service_application('missing-model', tools=(), system_prompt='',
                response_format=None, settings=settings) as app:
            project = await app.projects.add_local(root, trusted=True, operation_id='P')
            inputs = app.environments._inputs
            ready, _ = inputs.records().reserve(project.project_id, project.revision, 'a'*32,
                (), settings.sandbox.git)
            directory = inputs.directory(project.project_id, ready.input_id)
            directory.mkdir(parents=True)
            target = directory/'probe.bin'
            target.write_bytes(b'kept on failure')
            with monkeypatch.context() as patch:
                patch.setattr(os, 'unlink', occupied)
                with pytest.raises(PermissionError) as caught:
                    await app.environments.retire_input(project.project_id, ready.input_id)
                assert caught.value is failure
                assert await app.environments.input(project.project_id, ready.input_id) == ready
                assert target.read_bytes() == b'kept on failure'
            messages = [record.getMessage() for record in caplog.records
                if record.getMessage().startswith('git_input_directory_cleanup_failed ')]
            assert len(messages) == 1
            assert 'operation=unlink error_type=PermissionError' in messages[0]
            assert 'winerror=32 relative_path="probe.bin"' in messages[0]
            assert 'file_kind=regular' in messages[0]
            assert str(tmp_path) not in caplog.text and 'PRIVATE_' not in caplog.text
            assert await app.environments.retire_input(project.project_id, ready.input_id) is True
    asyncio.run(scenario())


def test_input_cleanup_log_failure_preserves_original_error(tmp_path, monkeypatch, caplog):
    import errno
    from types import SimpleNamespace
    from naiyue_test_agent.application.environments import inputs as module

    service = module.InputService(SimpleNamespace(projects=SimpleNamespace(data_dir=tmp_path)), None)
    directory = service.directory('project', 'a'*32)
    directory.mkdir(parents=True)
    original = PermissionError(errno.EACCES, 'PRIVATE_BODY')
    attempts = []

    def refuse(path, **kwargs):
        attempts.append(path)
        raise original

    def log_failure(*args, **kwargs):
        raise RuntimeError('PRIVATE_LOG_BODY')

    monkeypatch.setattr(module.shutil, 'rmtree', refuse)
    monkeypatch.setattr(module.logger, 'error', log_failure)
    with pytest.raises(PermissionError) as caught:
        service._remove_directory('project', 'a'*32)
    assert caught.value is original
    assert attempts == [directory]
    assert directory.is_dir()
    assert 'diagnostic_log_failed' in caplog.text
    assert 'PRIVATE_' not in caplog.text
