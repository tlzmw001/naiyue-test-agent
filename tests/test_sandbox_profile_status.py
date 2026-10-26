"""Historical execution names come from saved facts, including failed preparation."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import pytest
from naiyue_test_agent.contracts.git_inputs import GitInputError
from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog


@pytest.mark.parametrize('before_container', [False, True])
def test_status_preserves_selected_profile_after_catalog_removal(tmp_path, monkeypatch, before_container):
    root = repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path, monkeypatch, stack)
            async with application(tmp_path, catalog()) as app:
                project = await app.projects.add_local(root, trusted=True, operation_id='P')
                host = await app.projects.open(project.project_id)
                if before_container:
                    async def unavailable(*args, **kwargs): raise GitInputError('git_input_unconfirmed')
                    monkeypatch.setattr(app.environments, 'prepare_input', unavailable)
                sid = await host.sessions.create_sandbox('fake', request_id=str(uuid4()))
                path = tmp_path/'settings.toml'; path.write_text('')
                await app.environments.refresh(path)
                status = (await host.sessions.status(sid))['execution']
                assert status['profile_id'] == 'python-tests'
                assert status['profile_name'] == 'Python tests'
                assert len(status['profile_revision']) == 64
                assert not {'settings', 'image', 'context'} & status.keys()
    asyncio.run(scenario())
