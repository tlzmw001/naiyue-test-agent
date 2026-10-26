"""An idle check must not mark healthy background activity uncertain."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.sandbox import SandboxError
from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog


def test_idle_check_does_not_interrupt_background_observation(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path,dict(catalog(),lifecycle=dict(idle_stop_seconds=30))) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                env=(await app.environments.list(project.project_id))[0]
                records=app.environments._service.records();records.save(env.changed(last_used_at=100.0))
                ops=app.environments._operations;ident='b'*32
                row=ops.records().reserve(env.binding_id,project.project_id,sid,ident,'shell',{}, {})
                row=ops.records().save(env.binding_id,ident,dict(state='running',stale=False))
                original=app.environments._hosts.list_sessions
                queries=[]
                async def observe(project):
                    queries.append(project)
                    # A real refresh here would be rejected by the premature manage marker.
                    with app.environments._service.activity.use(env.binding_id,used=False):pass
                    return await original(project)
                monkeypatch.setattr(app.environments._hosts,'list_sessions',observe)
                await app.environments.sweep_idle(now=200.0)
                assert queries==[]  # A known unsettled operation is checked before acquiring management.
                assert ops.records().get(env.binding_id,ident)==row
                with app.environments._service.activity.manage(env.binding_id):
                    with pytest.raises(SandboxError,match='env_busy'):ops.refresh(env.binding_id,ident)
                assert ops.records().get(env.binding_id,ident)==row
                with records.database.transaction(write=True) as connection:
                    connection.execute('DELETE FROM sandbox_operations WHERE binding_id=? AND operation_id=?',(env.binding_id,ident))
    asyncio.run(scenario())
