import asyncio
from contextlib import ExitStack
from threading import Event
import shutil
import pytest
from git_input_helpers import repository,git
from sandbox_session_helpers import SandboxTransport
from test_sandbox_sessions import application
from test_git_delivery_service import prepare
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.execution.git_delivery import GitDelivery


@pytest.mark.parametrize('change',['relocate','rename','remove','close'])
def test_delivery_reservation_blocks_project_changes_even_without_hot_host(tmp_path,monkeypatch,change):
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
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                if change!='close':await app.projects.close(project.project_id)
                moved=tmp_path/'moved';shutil.copytree(root,moved)
                task=asyncio.create_task(app.environments.deliver(binding,'c'*32,
                    dict(operation_id='d'*32,action='branch',branch='reserved')))
                try:
                    assert await asyncio.to_thread(entered.wait,20)
                    with pytest.raises(ProjectError,match='project_in_use'):
                        if change=='relocate':await app.projects.relocate(project.project_id,expected_root=root,root=moved,trusted=True,operation_id='M')
                        elif change=='rename':await app.projects.rename(project.project_id,'changed',operation_id='M')
                        elif change=='remove':await app.projects.remove(project.project_id,operation_id='M')
                        else:await app.projects.close(project.project_id)
                finally:
                    release.set();await task
                assert (await app.projects.get(project.project_id)).revision==project.revision
                assert git(root,'rev-parse','refs/heads/reserved')
    asyncio.run(scenario())
