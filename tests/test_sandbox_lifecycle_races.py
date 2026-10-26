"""Execution and long transfers hold a lease until their full operation settles."""
import asyncio
from contextlib import ExitStack
from threading import Event
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport,OfflineModel
from test_sandbox_profile_sessions import application
from test_sandbox_profiles import catalog
from naiyue_test_agent.contracts.sandbox import SandboxError


@pytest.mark.parametrize('mode',['start','regenerate','capture'])
def test_active_execution_rejects_management_until_settled(tmp_path,monkeypatch,mode):
    root=repository(tmp_path)
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        class Model(OfflineModel):
            async def _agenerate(self,*args,**kwargs):
                if release.is_set():return await super()._agenerate(*args,**kwargs)
                entered.set();await release.wait()
                return await super()._agenerate(*args,**kwargs)
        model=Model(responses=[AIMessage(content='done')])
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack,model)
            async with application(tmp_path,catalog()) as app:
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                if mode=='regenerate':
                    release.set();await host.runs.start(sid,[('user','task')]);release.clear()
                    old=host.sessions._persistence.storage.replies.latest(sid)
                    work=host.runs.regenerate(sid,request_id=str(uuid4()),expected_reply_id=old['reply_id'],expected_conversation_revision=0)
                elif mode=='capture':
                    started,finish=Event(),Event()
                    def capture(*args):started.set();assert finish.wait(10);return 'captured'
                    monkeypatch.setattr(app.environments._results,'capture',capture)
                    work=app.environments.capture_result(binding,result_id='a'*32,include_untracked=[])
                else:work=host.runs.start(sid,[('user','task')])
                task=asyncio.create_task(work)
                try:
                    if mode=='capture':assert await asyncio.to_thread(started.wait,10)
                    else:await asyncio.wait_for(entered.wait(),10)
                    assert (await app.environments.management())['items'][0]['busy']
                    for action in ('stop','destroy'):
                        with pytest.raises(SandboxError,match='env_busy'):
                            await app.environments.manage(binding,action,confirm_destroy=True)
                finally:
                    if mode=='capture':finish.set()
                    else:release.set()
                    await task
                assert not (await app.environments.management())['items'][0]['busy']
    asyncio.run(scenario())
