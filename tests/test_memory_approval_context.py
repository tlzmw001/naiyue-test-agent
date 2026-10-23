"""A paused logical turn reuses its bounded memory parts without another recall."""

import asyncio

from langchain_core.messages import AIMessage,SystemMessage
from naiyue_test_agent.tools.read import create_session_read_tool
from naiyue_test_agent.tools.memory import create_memory_tool
from test_async_application import StreamingToolModel,call
from test_memory_tool import opened
from test_memory_jobs import response
from test_memory_application import config


def test_approval_resume_reuses_frozen_directory_and_releases_explicit_refresh(monkeypatch,tmp_path):
    async def scenario():
        root=tmp_path/'workspace';root.mkdir()
        outside=tmp_path/'outside.txt';outside.write_text('outside')
        model=StreamingToolModel(replies=[call('memory',dict(action='refresh_overview')),
            call('read',dict(path=str(outside))),AIMessage(content='根轮完成'),response()])
        async with opened(monkeypatch,root,model,settings=config(root),tools=[create_memory_tool(),create_session_read_tool()]) as app:
            other=await app.sessions.create_workspace('A',root)
            sid=await app.sessions.create_workspace('A',root)
            recalls=[];jobs=app.sessions._sessions.memory_jobs;prepare=jobs.prepare
            def counted(*args,**kwargs):
                recalls.append(args[0]);return prepare(*args,**kwargs)
            monkeypatch.setattr(jobs,'prepare',counted)
            first=await app.runs.start(sid,[('user','整理工作后读取文件')])
            assert first['approval_requests'] and len(model.seen)==2
            page=await app.tasks.list(session_id=sid,workspace_root=root)
            assert page['active_count']==1
            frozen=next(m.content for m in model.seen[0] if isinstance(m,SystemMessage))
            assert other in frozen and 'Other session directory' in frozen
            result=await app.runs.resume_approval(sid,['reject'])
            assert result['messages'][-1].content=='根轮完成'
            resumed=next(m.content for m in model.seen[2] if isinstance(m,SystemMessage))
            assert 'Other session directory' in resumed and other in resumed
            assert recalls==[sid]
            await jobs.wait_idle()
            assert len(model.seen)==4 and (await app.tasks.list(session_id=sid))['items'][0]['result_status']=='completed'
    asyncio.run(scenario())
