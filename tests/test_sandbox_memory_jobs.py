"""GitSource memory jobs use project identity and never need a Host directory."""
import asyncio
from uuid import uuid4
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.storage.project_store import ProjectStore
from test_async_application import StreamingToolModel
from test_memory_jobs import response
from test_memory_lifecycle import source


def test_git_sandbox_overview_saved_by_controller_without_execution_path(tmp_path):
    async def scenario():
        project=str(uuid4())
        with ProjectStore(tmp_path/'project.sqlite') as storage:
            storage.sessions.bind_git_project(project)
            storage.sessions.create('s',dict(thread_id='t',model_id='M',project_id=project,workspace_root=None,
                execution_root_kind=None,execution_binding='sandbox',permission_mode='Default',active_tool_names=['memory'],
                execution_setup=dict(model_id='M',include_untracked=[],input_id='a'*32,binding_id='b'*32)))
            memory=MemoryAccess(storage,MemorySettings(auto_enabled=False,related_limit=0),tmp_path/'data')
            model=StreamingToolModel(replies=[response()]);jobs=MemoryJobs(memory,ModelCatalog('M',{'M':model}))
            try:
                source(memory,'s',text='已在沙箱中完成检查',at=1000.)
                jobs.prepare('s',None,[],model)
                accepted=await jobs.request(source_session_id='s',target_session_id='s',workspace_root=None,
                    origin_run_id='R',model_id='M',tool_call_id='refresh')
                await jobs.after_finish('s','R','completed',model_id='M');await jobs.wait_idle()
                output=await jobs.output(accepted['task_id'],session_id='s')
                assert output['workspace_root'] is None and output['result_status']=='completed',output
                overview=storage.memory.overview('s')
                assert overview['overview_status']=='generated' and overview['overview']['next_step']=='继续验证'
                assert '已在沙箱中完成检查' in str(model.seen[0])
                assert (await jobs.list(session_id='s'))['items'][0]['task_id']==accepted['task_id']
            finally:
                await jobs.aclose();memory.close()
    asyncio.run(scenario())
