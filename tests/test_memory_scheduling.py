"""Scheduling boundaries use saved root completion, never tool-return timing."""

import asyncio

import pytest

from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
from naiyue_test_agent.storage.memory_sql import memory_overview_store as store
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.model_access import ModelCatalog
from test_memory_jobs import jobs
from test_memory_lifecycle import source


async def register(service, root, sid='s', run='R'):
    return await service.request(source_session_id=sid,target_session_id=sid,
        workspace_root=root,origin_run_id=run,model_id='A')


def test_approval_resume_keeps_pending_request_without_resetting_deadline(jobs):
    async def scenario():
        storage,access,model,root=jobs
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            service.prepare('s',root,[],model)
            accepted=await register(service,root)
            job=service._entries[accepted['task_id']];deadline=job.deadline
            source(access,'s',text='第一段工作')
            await service.after_finish('s','R','interrupted',model_id='A')
            assert not job.control.requested and not job.released and not model.seen
            await service.after_finish('s','resumed-R','completed',model_id='A')
            await service.wait_idle()
            assert job.result_status=='completed' and job.deadline==deadline and not job.pending_runs
            assert len(model.seen)==1
        finally:await service.aclose()
    asyncio.run(scenario())


def test_queue_full_does_not_advance_baseline_or_call_model(jobs):
    async def scenario():
        storage,access,model,root=jobs
        access.settings=MemorySettings(auto_enabled=False,max_pending_tasks=1)
        storage.sessions.create('other',dict(thread_id='other',model_id='A',workspace_root=root,
            active_tool_names=['memory'],permission_mode='Default'))
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            service.prepare('s',root,[],model);service.prepare('other',root,[],model)
            before=storage.memory.ensure('other')
            await register(service,root)
            source(access,'other',text='队列满时保留的新增材料')
            with pytest.raises(MemoryReadError,match='memory_queue_full'):
                await register(service,root,'other')
            assert storage.memory.ensure('other')==before and not model.seen
            await service.stop(next(iter(service._entries)))
            accepted=await register(service,root,'other')
            await service.after_finish('other','R','completed',model_id='A')
            await service.wait_idle()
            assert service._entries[accepted['task_id']].result_status=='completed'
            assert '保留的新增材料' in str(model.seen[-1])
        finally:await service.aclose()
    asyncio.run(scenario())


def test_failed_admission_save_never_releases_worker(jobs,monkeypatch):
    async def scenario():
        storage,access,model,root=jobs
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            service.prepare('s',root,[],model)
            accepted=await register(service,root)
            source(access,'s')
            def fail(*args,**kwargs):raise OSError('private provider detail')
            monkeypatch.setattr(store,'accept',fail)
            await service.after_finish('s','R','completed',model_id='A')
            await service.wait_idle()
            job=service._entries[accepted['task_id']]
            assert not job.released and not model.seen
            assert job.result_status=='failed' and job.termination=='admission_save_failed'
            assert storage.memory.ensure('s')['next_sequence']==0
        finally:await service.aclose()
    asyncio.run(scenario())


def test_terminal_revision_changes_only_for_scoped_real_completion(jobs):
    async def scenario():
        storage,access,model,root=jobs
        storage.sessions.create('other',dict(thread_id='other',model_id='A',workspace_root=root,
            active_tool_names=['memory'],permission_mode='Default'))
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            accepted=await register(service,root)
            page=await service.list(session_id='s',workspace_root=root)
            assert page['terminal_revision']==0
            await service.stop(accepted['task_id'])
            assert (await service.list(session_id='s'))['terminal_revision']>0
            assert (await service.list(session_id='other'))['terminal_revision']==0
            with pytest.raises(Exception,match='active memory tasks'):
                pending=await register(service,root,'other')
                with service.deletion_guard('other'):pass
            await service.stop(pending['task_id'])
            with service.deletion_guard('other'):pass
        finally:await service.aclose()
    asyncio.run(scenario())


def test_explicit_ranges_are_not_silently_merged_or_dropped(jobs):
    async def scenario():
        _,access,model,root=jobs
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            await service.request(source_session_id='s',target_session_id='s',workspace_root=root,
                origin_run_id='R',model_id='A',end_sequence=1)
            with pytest.raises(MemoryReadError,match='memory_target_pending'):
                await service.request(source_session_id='s',target_session_id='s',workspace_root=root,
                    origin_run_id='R',model_id='A',end_sequence=2)
            with pytest.raises(MemoryReadError,match='memory_target_pending'):
                await register(service,root)
            assert not model.seen
        finally:await service.aclose()
    asyncio.run(scenario())


def test_directory_marker_failure_cannot_leave_failed_root_request_alive(jobs,monkeypatch,caplog):
    async def scenario():
        storage,access,model,root=jobs
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        try:
            service.prepare('s',root,[],model);service.directory_attempt('s')
            accepted=await register(service,root,run='failed-R')
            def fail(*args,**kwargs):raise OSError('PRIVATE_MARKER')
            monkeypatch.setattr(store,'mark_directory',fail)
            await service.after_finish('s','failed-R','failed',model_id='A')
            await service.wait_idle()
            job=service._entries[accepted['task_id']]
            assert job.result_status=='stopped' and not job.pending_runs
            source(access,'s',text='以后新工作')
            await service.after_finish('s','later-R','completed',model_id='A')
            assert not model.seen
            assert 'memory_directory_marker_failed' in caplog.text and 'PRIVATE_MARKER' not in caplog.text
        finally:await service.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('paused_read',[False,True])
def test_deletion_guard_reserves_admission_across_async_metadata_reads(jobs,monkeypatch,paused_read):
    async def scenario():
        from naiyue_test_agent.application.memory import memory_jobs as module
        storage,access,model,root=jobs
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        entered,release=asyncio.Event(),asyncio.Event()
        original=module.run_blocking
        async def paused(call,*args,**kwargs):
            if paused_read and call==storage.sessions.get and not entered.is_set():
                entered.set();await release.wait()
            return await original(call,*args,**kwargs)
        monkeypatch.setattr(module,'run_blocking',paused)
        try:
            if paused_read:
                pending=asyncio.create_task(register(service,root))
                await asyncio.wait_for(entered.wait(),5)
            with service.deletion_guard('s'):
                if paused_read:release.set()
                with pytest.raises(MemoryReadError,match='memory_admission_closed'):
                    await pending if paused_read else await register(service,root)
            # A failed delete that exits its guard must not permanently block the session.
            accepted=await register(service,root)
            await service.stop(accepted['task_id'])
            assert not model.seen
        finally:
            release.set();await service.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('deleted',['s','target'])
def test_completed_delete_invalidates_an_already_read_request_before_registration(jobs,monkeypatch,deleted):
    async def scenario():
        from langgraph.checkpoint.sqlite import SqliteSaver
        from naiyue_test_agent.application.memory import memory_jobs as module
        storage,access,model,root=jobs
        SqliteSaver(storage.database.connection).setup()
        storage.sessions.create('target',dict(thread_id='target',model_id='A',workspace_root=root,
            active_tool_names=['memory'],permission_mode='Default'))
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        original=module.run_blocking;entered,release=asyncio.Event(),asyncio.Event()
        async def paused(call,*args,**kwargs):
            result=await original(call,*args,**kwargs)
            if call==storage.sessions.get_workspace_binding:
                entered.set();await release.wait()
            return result
        monkeypatch.setattr(module,'run_blocking',paused)
        pending=asyncio.create_task(service.request(source_session_id='s',target_session_id='target',
            workspace_root=root,origin_run_id='R',model_id='A'))
        try:
            await asyncio.wait_for(entered.wait(),5)
            with service.deletion_guard(deleted):
                storage.runs.delete(deleted,[]);service.forget_session(deleted)
            release.set()
            with pytest.raises(MemoryReadError,match='memory_admission_closed'):await pending
            assert not service._entries and not model.seen
        finally:
            release.set();await asyncio.gather(pending,return_exceptions=True);await service.aclose()
    asyncio.run(scenario())
