"""Prepared sessions retain their selected template across retries and reopen."""
import asyncio
from contextlib import ExitStack
import json
from uuid import uuid4

import pytest

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profiles import catalog
from naiyue_test_agent.application.service_application import open_service_application
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.settings import AppSettings
from naiyue_test_agent.tools import create_builtin_tools


def application(tmp_path, configuration):
    settings = AppSettings.model_validate(dict(storage=dict(data_dir=str(tmp_path/'data')),
        memory=dict(auto_enabled=False), sandbox=configuration, permissions=dict(mode='BypassApprovals')))
    return open_service_application('unused', tools=create_builtin_tools(settings.tools),
        system_prompt='', response_format=None, settings=settings)


@pytest.mark.parametrize('remove', [False, True])
def test_default_selection_is_saved_and_retry_never_resolves_new_default(tmp_path, monkeypatch, remove):
    root = repository(tmp_path)

    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                request = str(uuid4())
                sid = await host.sessions.create_sandbox('fake', request_id=request)
                assert (await host.sessions.status(sid))['execution']['state'] == 'ready'
                row = host.sessions._sessions._persistence.storage.sessions.get(sid)
                saved = json.loads(row['execution_setup_json'])['selection']
                assert saved['profile_id'] == 'python-tests'
                assert saved['settings']['image'] == 'fixture:python'
                service = app.environments._service
                record = service.get(json.loads(row['execution_setup_json'])['binding_id'])
                assert record.profile_revision == saved['revision']
                assert record.settings['image'] == 'fixture:python'
                assert not {'profiles', 'default_profile'} & record.settings.keys()
                service.configuration = AppSettings.model_validate({'sandbox': {} if remove else catalog('frontend-tests')}).sandbox
                assert await host.sessions.create_sandbox('fake', request_id=request) == sid
                binding = (await host.sessions.status(sid))['execution']['binding_id']
                await app.environments.start(binding)
                assert len(transport.created) == 1
                assert (await host.sessions.status(sid))['execution']['state'] == 'ready'
                if not remove:
                    other = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                    setup = json.loads(host.sessions._sessions._persistence.storage.sessions.get(other)['execution_setup_json'])
                    assert setup['selection']['profile_id'] == 'frontend-tests'
                    assert service.get(setup['binding_id']).settings['image'] == 'fixture:node'
    asyncio.run(scenario())


def test_selection_survives_service_reopen_with_no_current_templates(tmp_path, monkeypatch):
    root = repository(tmp_path)

    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                request = str(uuid4())
                sid = await host.sessions.create_sandbox('fake', request_id=request)
            async with application(tmp_path, {}) as app:
                host = await app.projects.open(project.project_id)
                assert await host.sessions.create_sandbox('fake', request_id=request) == sid
                binding = (await host.sessions.status(sid))['execution']['binding_id']
                await app.environments.start(binding)
                assert (await host.sessions.prepare_environment(sid))['state'] == 'ready'
                assert len(transport.created) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('registered', [False, True])
def test_legacy_setup_uses_only_original_registration(tmp_path, monkeypatch, registered):
    root = repository(tmp_path)

    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, dict(image='fixture:legacy')) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                service = app.environments._service
                metadata = host.sessions._sessions._persistence.storage.sessions
                if registered:
                    sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                    row = metadata.get(sid)
                    setup = json.loads(row['execution_setup_json'])
                    setup.pop('selection')
                    record = service.get(setup['binding_id'])
                    service.records().save(record.changed(profile_id=None, profile_name=None, profile_revision=None))
                    with metadata.database.transaction(write=True) as connection:
                        connection.execute('UPDATE naiyue_sessions SET execution_setup_json=? WHERE session_id=?',
                            (json.dumps(setup), sid))
                else:
                    setup = dict(model_id='fake', include_untracked=[], input_id='a'*32, binding_id='b'*32)
                    sid = host.sessions._sessions._create_session('fake', None, execution_setup=setup)
                service.configuration = AppSettings.model_validate({'sandbox': catalog('frontend-tests')}).sandbox
                count = len(transport.created)
                if registered:
                    assert (await host.sessions.prepare_environment(sid))['state'] == 'ready'
                    assert service.get(setup['binding_id']).settings['image'] == 'fixture:legacy'
                else:
                    with pytest.raises(SandboxError, match='env_legacy_setup_unavailable'):
                        await host.sessions.prepare_environment(sid)
                    assert (await host.sessions.status(sid))['session_id'] == sid
                    with pytest.raises(GitInputError):
                        await app.environments.input(project.project_id, setup['input_id'])
                assert len(transport.created) == count
    asyncio.run(scenario())


def test_preparation_retry_before_container_creation_keeps_original_image_and_git_policy(tmp_path, monkeypatch):
    root = repository(tmp_path)

    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            configured = dict(catalog(), git=dict(max_files=20))
            async with application(tmp_path, configured) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                request = str(uuid4())
                original = app.environments.prepare_input

                async def unavailable(*args, **kwargs):
                    raise GitInputError('git_input_unconfirmed')

                monkeypatch.setattr(app.environments, 'prepare_input', unavailable)
                sid = await host.sessions.create_sandbox('fake', request_id=request)
                assert transport.created == []
                app.environments._service.configuration = AppSettings.model_validate(
                    {'sandbox': dict(catalog('frontend-tests'), git=dict(max_files=30))}).sandbox
                monkeypatch.setattr(app.environments, 'prepare_input', original)
                assert await host.sessions.create_sandbox('fake', request_id=request) == sid
                setup = json.loads(host.sessions._sessions._persistence.storage.sessions.get(sid)['execution_setup_json'])
                record = app.environments._service.get(setup['binding_id'])
                assert record.settings['image'] == 'fixture:python'
                assert record.settings['git']['max_files'] == 20
                assert (await app.environments.input(project.project_id, setup['input_id'])).settings['max_files'] == 20
                assert len(transport.created) == 1
    asyncio.run(scenario())
