import asyncio
from contextlib import ExitStack
from threading import Event
import pytest
from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_sessions import application
from test_git_delivery_service import prepare
from naiyue_test_agent.application.errors import SessionBusyError
from naiyue_test_agent.execution.git_delivery import GitDelivery


def test_delivery_blocks_run_and_environment_management_until_required_save(tmp_path,monkeypatch):
    root=repository(tmp_path);entered=Event();release=Event();normal=GitDelivery.execute
    def pause(self,*args,**kwargs):
        entered.set();assert release.wait(20)
        return normal(self,*args,**kwargs)
    monkeypatch.setattr(GitDelivery,'execute',pause)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            async with application(tmp_path) as app:
                project,host,sid,_=await prepare(app,root,tmp_path)
                task=asyncio.create_task(host.sessions.deliver(sid,'c'*32,
                    dict(operation_id='d'*32,action='branch',branch='reserved')))
                try:
                    assert await asyncio.to_thread(entered.wait,20)
                    with pytest.raises(SessionBusyError):await host.runs.start(sid,[('user','cannot run during delivery')])
                    with pytest.raises(SessionBusyError):await host.sessions.environment_action(sid,'stop')
                    # Same-project history remains readable while Git is blocked.
                    assert (await app.projects.list_sessions(project.project_id))[0]['session_id']==sid
                finally:
                    release.set();await task
                with host.sessions._sessions.manage_environment(sid):pass
    asyncio.run(scenario())
