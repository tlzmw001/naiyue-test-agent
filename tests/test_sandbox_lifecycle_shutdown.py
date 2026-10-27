"""Shutdown attempts every original container and preserves the first failure."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4
import pytest
from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.storage.project_registry import ProjectRegistry
from naiyue_test_agent.storage.environment_registry import EnvironmentRegistry


def test_shutdown_stops_other_containers_after_first_failure(tmp_path,monkeypatch,caplog):
    root=repository(tmp_path);failure=SandboxError('env_unavailable')
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            with pytest.raises(SandboxError) as caught:
                async with application(tmp_path,catalog()) as app:
                    project=await app.projects.add_local(root,trusted=True,operation_id='P')
                    host=await app.projects.open(project.project_id)
                    for _ in range(2):await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                    rows=await app.environments.list(project.project_id);first,second=[r.binding_id for r in rows]
                    original=app.environments._service._engine
                    def engine(row):
                        value=original(row);act=value.action
                        def action(row,verb):
                            if row.binding_id==first:raise failure
                            return act(row,verb)
                        value.action=action;return value
                    monkeypatch.setattr(app.environments._service,'_engine',engine)
            assert caught.value is failure and second in transport.stopped and first not in transport.stopped
            registry=ProjectRegistry(tmp_path/'data/projects.sqlite')
            try:
                records=EnvironmentRegistry(registry.database)
                assert records.get(first).lifecycle=='unknown'
                assert records.get(second).lifecycle=='stopped'
                assert records.usage()==dict(running=1,retained=2)
            finally:registry.close()
    asyncio.run(scenario())
    assert 'sandbox_shutdown_container_unconfirmed' in caplog.text
