"""Complete user-facing memory flows through the actual persistent Agent graph."""

import asyncio
import json
import pytest

from langchain_core.messages import AIMessage, SystemMessage

from naiyue_test_agent.settings import AppSettings
from test_async_application import StreamingToolModel,call
from test_memory_tool import opened,feedback
from test_memory_jobs import response


def config(root,**memory):
    return AppSettings.model_validate({'storage':{'data_dir':str(root/'data')},'memory':{'auto_enabled':False,**memory}})


def test_refresh_finishes_root_before_independent_model_and_persists_overview(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',dict(action='refresh_overview'),'refresh'),
            AIMessage(content='任务已登记'),response()])
        async with opened(monkeypatch,tmp_path,model,settings=config(tmp_path)) as app:
            sid=await app.acreate_workspace_session('A',tmp_path)
            events=[];app.subscribe_events(events.append)
            result=await app.arun(sid,[('user','请整理当前进展')])
            assert result['messages'][-1].content=='任务已登记'
            receipt=json.loads(feedback(result)[0].content)
            assert receipt['status']=='accepted'
            await app._sessions.memory_jobs.wait_idle()
            task=await app.aget_background_task_output(receipt['task_id'],session_id=sid,workspace_root=tmp_path)
            assert task['kind']=='memory' and task['result_status']=='completed' and task['cleanup_confirmed']
            assert len(model.seen)==3
            # Worker publishes only to its own usage/diagnostic consumers.
            assert len({e.run_id for e in events})==1 and all(getattr(e.data,'mode',None)!='memory' for e in events)
            from naiyue_test_agent.application.memory.memory_overview_store import overview
            assert app._storage.memory.overview(sid)['overview_status']=='generated'
            page=await app.alist_background_tasks(session_id=sid,workspace_root=tmp_path)
            assert page['items'][0]['task_id']==receipt['task_id'] and page['active_count']==0
    asyncio.run(scenario())


@pytest.mark.parametrize('thresholds',[dict(trigger_turns=1,trigger_chars=99999),dict(trigger_turns=99999,trigger_chars=1)])
def test_automatic_or_threshold_and_failure_does_not_repeat_old_range(monkeypatch,tmp_path,thresholds):
    async def scenario():
        model=StreamingToolModel(replies=[AIMessage(content='第一轮完成'),AIMessage(content='invalid JSON'),
            AIMessage(content='第二轮完成'),response()])
        async with opened(monkeypatch,tmp_path,model,settings=config(tmp_path,auto_enabled=True,**thresholds)) as app:
            sid=await app.acreate_workspace_session('A',tmp_path)
            await app.arun(sid,[('user','第一轮')]);await app._sessions.memory_jobs.wait_idle()
            first=await app.alist_background_tasks(session_id=sid,workspace_root=tmp_path)
            assert first['items'][0]['result_status']=='failed' and len(model.seen)==2
            await app.arun(sid,[('user','第二轮')]);await app._sessions.memory_jobs.wait_idle()
            assert len(model.seen)==4
            assert '第一轮完成' not in str(model.seen[-1]) and '第二轮完成' in str(model.seen[-1])
            assert (await app.alist_background_tasks(session_id=sid,workspace_root=tmp_path))['items'][0]['result_status']=='completed'
    asyncio.run(scenario())


def test_new_session_directory_is_first_request_only_and_history_reads_overview(monkeypatch,tmp_path):
    async def scenario():
        model=StreamingToolModel(replies=[call('memory',dict(action='refresh_overview')),AIMessage(content='已做测试'),
            response(),AIMessage(content='知道下一步'),AIMessage(content='继续工作')])
        async with opened(monkeypatch,tmp_path,model,settings=config(tmp_path)) as app:
            first=await app.acreate_workspace_session('A',tmp_path)
            await app.arun(first,[('user','本轮完成一个检查，请整理')]);await app._sessions.memory_jobs.wait_idle()
            second=await app.acreate_workspace_session('A',tmp_path)
            await app.arun(second,[('user','继续')])
            system=next(m.content for m in model.seen[-1] if isinstance(m,SystemMessage))
            assert 'Other session directory' in system and '测试任务' in system and first in system
            await app.arun(second,[('user','再继续')])
            system=next(m.content for m in model.seen[-1] if isinstance(m,SystemMessage))
            assert 'Other session directory' not in system
            from naiyue_test_agent.tools.history import HistoryInput
            from naiyue_test_agent.tools.history_settings import HistorySettings
            directory=app._storage.conversation.read_history_tool(second,app._storage.sessions.get(second)['project_id'],HistoryInput(action='list_sessions').model_dump(),HistorySettings())
            assert directory['items'][0]['overview_status']=='generated'
            detail=app._storage.conversation.read_history_tool(second,app._storage.sessions.get(second)['project_id'],HistoryInput(action='read',session_id=first).model_dump(),HistorySettings())
            assert detail['overview']['next_step']=='继续验证'
    asyncio.run(scenario())
