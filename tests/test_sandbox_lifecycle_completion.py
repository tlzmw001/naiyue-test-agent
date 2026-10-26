"""The first settled completion starts idle time; subsequent reads do not."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4
from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from naiyue_test_agent.tools.settings import BashSettings


def test_background_completion_renews_idle_once(tmp_path,monkeypatch):
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
                metadata=dict(offset=0,saved_bytes=0,max_archive_bytes=1000,tail_limit=1000,
                    settings=BashSettings().model_dump(mode='json'),background=True)
                row=ops.records().reserve(env.binding_id,project.project_id,sid,ident,'shell',{},metadata)
                row=ops.records().save(env.binding_id,ident,dict(state='running',stale=False))
                monkeypatch.setattr(ops,'_rpc',lambda *_a,**_k:dict(row,state='completed',observed_bytes=0))
                with monkeypatch.context() as patch:
                    patch.setattr('naiyue_test_agent.application.environments.activity.time.time',lambda:1000.0)
                    assert ops.finished(await asyncio.to_thread(ops.refresh,env.binding_id,ident))
                assert (await app.environments.get(env.binding_id)).last_used_at==1000.0
                await asyncio.to_thread(ops.refresh,env.binding_id,ident)
                assert (await app.environments.get(env.binding_id)).last_used_at==1000.0
                await app.environments.sweep_idle(now=1001.0)
                assert (await app.environments.get(env.binding_id)).lifecycle=='running'
                await app.environments.sweep_idle(now=1031.0)
                assert (await app.environments.get(env.binding_id)).lifecycle=='stopped'
    asyncio.run(scenario())
