"""Explicit requests bind saved selections; retries never consult new templates."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.session_environment import saved_setup
from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profiles import catalog
from test_sandbox_profile_sessions import application


def test_explicit_selection_retries_after_refresh_and_service_reopen(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            key = str(uuid4())
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                options = await app.environments.options(project.project_id)
                profile = next(p for p in options['profiles'] if p['profile_id'] == 'frontend-tests')
                args = dict(request_id=key, profile_id=profile['profile_id'], expected_profile_revision=profile['revision'])
                sid = await host.sessions.create_sandbox('fake', **args)
                setup = saved_setup(host.sessions._persistence.storage.sessions.get(sid))
                assert setup.selection.profile_id == 'frontend-tests'
                assert setup.requested_profile.profile_id == 'frontend-tests'
                record = await app.environments.get(setup.binding_id)
                assert record.settings['image'] == 'fixture:node'
                path = tmp_path/'settings.toml'; path.write_text('')
                assert (await app.environments.refresh(path))['profiles'] == []
                assert await host.sessions.create_sandbox('fake', **args) == sid
                with pytest.raises(SandboxError, match='env_operation_conflict'):
                    await host.sessions.create_sandbox('fake', request_id=key)
                with pytest.raises(SandboxError, match='env_operation_conflict'):
                    await host.sessions.create_sandbox('fake', **{**args, 'profile_id': 'python-tests'})
            async with application(tmp_path, {}) as reopened:
                host = await reopened.projects.open(project.project_id)
                assert await host.sessions.create_sandbox('fake', **args) == sid
                assert len(transport.created) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('profile_id,revision,code', [
    ('unknown', '0'*64, 'env_profile_unavailable'),
    ('frontend-tests', '0'*64, 'env_profile_changed'),
])
def test_unknown_or_stale_choice_fails_before_any_saved_side_effect(tmp_path, monkeypatch, profile_id, revision, code):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                with pytest.raises(SandboxError, match=code):
                    await host.sessions.create_sandbox('fake', request_id=str(uuid4()),
                        profile_id=profile_id, expected_profile_revision=revision)
                assert await host.sessions.list() == []
                assert app.environments._inputs.records().database.read_rows(
                    'SELECT input_id FROM git_inputs WHERE project_id=?', (project.project_id,)) == []
                assert transport.created == []
    asyncio.run(scenario())


def test_options_use_one_configuration_snapshot(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                service = app.environments._service
                from naiyue_test_agent.execution.git_repository import GitRepository
                from naiyue_test_agent.settings import AppSettings
                original = GitRepository.run
                def run(repo, *args, **kwargs):
                    service.configuration = AppSettings().sandbox
                    return original(repo, *args, **kwargs)
                monkeypatch.setattr(GitRepository, 'run', run)
                options = await app.environments.options(project.project_id)
                assert options['sandbox_available'] is True
                assert options['default_profile'] == 'python-tests'
                assert options['network'] == 'none'
                assert [p['profile_id'] for p in options['profiles']] == ['python-tests', 'frontend-tests']
                assert all(set(p) == {'profile_id', 'name', 'revision'} for p in options['profiles'])
    asyncio.run(scenario())


def test_actual_refresh_changes_fingerprint_and_requires_reconfirmation(tmp_path, monkeypatch):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                before = (await app.environments.options(project.project_id))['profiles'][1]
                path = tmp_path/'settings.toml'
                path.write_text('[sandbox]\ndefault_profile="frontend-tests"\ncpus=4.0\n'
                    '[sandbox.profiles.frontend-tests]\nname="Node updated"\nimage="fixture:new-node"\n')
                after = (await app.environments.refresh(path))['profiles'][0]
                assert after['revision'] != before['revision']
                with pytest.raises(SandboxError, match='env_profile_changed'):
                    await host.sessions.create_sandbox('fake', request_id=str(uuid4()),
                        profile_id=before['profile_id'], expected_profile_revision=before['revision'])
                assert await host.sessions.list() == [] and transport.created == []
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()),
                    profile_id=after['profile_id'], expected_profile_revision=after['revision'])
                setup = saved_setup(host.sessions._persistence.storage.sessions.get(sid))
                assert setup.selection.name == 'Node updated'
                assert setup.selection.settings.image == 'fixture:new-node'
                assert setup.selection.settings.cpus == 4.0
                assert (await host.sessions.status(sid))['execution']['state'] == 'ready'
    asyncio.run(scenario())


@pytest.mark.parametrize('args', [dict(profile_id='frontend-tests'),
    dict(expected_profile_revision='a'*64), dict(profile_id='../escape', expected_profile_revision='a'*64)])
def test_application_boundary_validates_explicit_profile_pair(tmp_path, monkeypatch, args):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                with pytest.raises(SandboxError, match='env_profile_selection_invalid'):
                    await host.sessions.create_sandbox('fake', request_id=str(uuid4()), **args)
                assert await host.sessions.list() == [] and transport.created == []
    asyncio.run(scenario())
