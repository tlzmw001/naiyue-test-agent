"""Service refresh and restart retain the selected remote, including old requests."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application

from naiyue_test_agent.contracts.session_environment import saved_setup


def test_saved_ssh_target_survives_refresh_retry_and_service_restart(tmp_path, monkeypatch):
    root = repository(tmp_path)
    first = dict(image='fixture:python', ssh=dict(host='first', docker_executable='/usr/bin/docker'))
    async def scenario():
        with ExitStack() as stack:
            transport = SandboxTransport(tmp_path, monkeypatch, stack)
            from naiyue_test_agent.application.environments import service
            base = service.DockerCLI
            constructed = []
            class CapturedCLI(base):
                def __init__(self, settings, cwd):
                    constructed.append(settings.ssh.host)
                    super().__init__(settings, cwd)
            monkeypatch.setattr(service, 'DockerCLI', CapturedCLI)
            request = str(uuid4())
            async with application(tmp_path, first) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                sid = await host.sessions.create_sandbox('fake', request_id=request)
                setup = saved_setup(host.sessions._persistence.storage.sessions.get(sid))
                assert setup.selection.settings.ssh.host == 'first'
                path = tmp_path/'refresh.toml'
                path.write_text('[sandbox]\nimage="fixture:python"\n[sandbox.ssh]\nhost="second"\n')
                await app.environments.refresh(path)
                assert await host.sessions.create_sandbox('fake', request_id=request) == sid
                other = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                second = saved_setup(host.sessions._persistence.storage.sessions.get(other))
                assert second.selection.settings.ssh.host == 'second'
                assert (await app.environments.get(setup.binding_id)).settings['ssh']['host'] == 'first'
                assert (await app.environments.get(second.binding_id)).settings['ssh']['host'] == 'second'
                assert len(transport.created) == 2
            async with application(tmp_path, {}) as reopened:
                host = await reopened.projects.open(project.project_id)
                assert await host.sessions.create_sandbox('fake', request_id=request) == sid
                constructed.clear()
                await reopened.environments.start(setup.binding_id)
                assert constructed and set(constructed) == {'first'}
                assert (await reopened.environments.get(setup.binding_id)).container_id == setup.binding_id*2
                assert len(transport.created) == 2
    asyncio.run(scenario())
