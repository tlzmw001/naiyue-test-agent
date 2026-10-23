"""Application-owned memory work: release after save, limits and optional failures."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.application.memory_jobs import MemoryJobs
from naiyue_test_agent.application.memory_access import MemoryAccess
from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.model_access import ModelCatalog
from test_memory_lifecycle import source
from test_async_application import StreamingToolModel


def response(**changes):
    value=dict(overview=dict(goal='测试任务',progress='完成一个检查',blockers='',next_step='继续验证'),proposals=[])
    value.update(changes)
    return AIMessage(content=json.dumps(value,ensure_ascii=False))


@pytest.fixture
def jobs(tmp_path):
    with SessionStorage(tmp_path/'sessions.sqlite') as storage:
        storage.bind_workspace(str(uuid4()),tmp_path)
        storage.create('s',dict(thread_id='s',model_id='A',workspace_root=tmp_path,active_tool_names=['memory'],permission_mode='Default'))
        access=MemoryAccess(storage,MemorySettings(auto_enabled=False),tmp_path/'data')
        model=StreamingToolModel(replies=[response()])
        yield storage,access,model,tmp_path
        access.close()


def test_explicit_waits_for_normal_saved_finish_then_overview_is_visible(jobs):
    async def scenario():
        storage,access,model,root=jobs
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            service.prepare('s',root,[],model)
            accepted=await service.request(source_session_id='s',target_session_id='s',workspace_root=root,
                origin_run_id='root-run',model_id='A',tool_call_id='refresh')
            assert accepted['status']=='accepted' and not model.seen
            source(access,'s',text='完成测试',at=1000.)
            await service.after_finish('s','root-run','completed',model_id='A')
            await service.wait_idle()
            row=await service.output(accepted['task_id'],session_id='s',workspace_root=root)
            assert row['result_status']=='completed' and row['cleanup_confirmed']
            assert len(model.seen)==1 and '完成测试' in str(model.seen[0])
            from naiyue_test_agent.application.memory_overview_store import overview
            saved=overview(storage,'s')
            assert saved['overview_status']=='generated' and saved['overview']['next_step']=='继续验证'
        finally:await service.aclose()
    asyncio.run(scenario())


def test_failed_root_cancels_pending_without_model_call(jobs):
    async def scenario():
        storage,access,model,root=jobs
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            accepted=await service.request(source_session_id='s',target_session_id='s',workspace_root=root,
                origin_run_id='R',model_id='A',tool_call_id='refresh')
            await service.after_finish('s','R','failed',model_id='A')
            await service.wait_idle()
            row=await service.output(accepted['task_id'],session_id='s',workspace_root=root)
            assert row['result_status']=='stopped' and not model.seen
        finally:await service.aclose()
    asyncio.run(scenario())


def test_invalid_model_is_not_silently_replaced(jobs):
    storage,access,model,_=jobs
    access.settings=MemorySettings(model_id='missing')
    with pytest.raises(ValueError,match='memory_model_unavailable'):MemoryJobs(access,ModelCatalog('A',{'A':model}))
