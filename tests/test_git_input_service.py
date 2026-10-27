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
