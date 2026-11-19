"""Wiki organization runs only after root release and never invokes legacy extraction."""
import asyncio
import pytest
from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_jobs import jobs
from memory_wiki_helpers import NOW,record


def test_real_prepare_initializes_trigger_baseline_but_preview_does_not(jobs):
    storage,access,model,root=jobs
    access.settings=access.settings.model_copy(update={'wiki_navigation_tokens':0})
    service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
    before=storage.database.connection.total_changes
    assert service.prepare('s',root,[],model,preview=True)==[]
    assert storage.memory.progress('s')==[]
    assert storage.database.connection.total_changes==before
    assert service.prepare('s',root,[],model)==[]
    progress,=storage.memory.progress('s')
    assert progress['completed_turns']==progress['accepted_turns']==0
    assert progress['completed_chars']==progress['accepted_chars']==0
    assert not model.seen


def test_organize_registration_validates_pages_waits_for_root_and_never_runs_legacy_worker(jobs):
    async def scenario():
        storage,access,model,root=jobs
        workspace=storage.sessions.get_workspace_binding()['workspace_id']
        storage.memory.prepare_wiki(now=NOW)
        page=WikiWriter.new_page('working_agreements','后台规则','失败处理')
        with storage.memory.wiki_edit('workspace',workspace,now=NOW,capacity=30) as writer:
            writer.apply(pages=[page],memories=[record(page['page_id'],workspace_id=workspace)])
        from test_memory_wiki_worker import finish
        model.replies=[finish()]
        service=MemoryJobs(access,ModelCatalog('A',{'A':model}))
        kwargs=dict(source_session_id='s',target_session_id='s',workspace_root=root,origin_run_id='run',model_id='A')
        try:
            with pytest.raises(MemoryReadError,match='memory_not_found'):
                await service.request(**kwargs,wiki_request=dict(action='organize',scope='workspace',page_ids=['a'*32]))
            result=await service.request(**kwargs,wiki_request=dict(action='organize',scope='workspace',page_ids=[page['page_id']]))
            assert result['status']=='accepted' and not model.seen
            job=service._entries[result['task_id']]
            assert not job.released and not job.cleanup_confirmed
            await service.after_finish('s','run','completed',model_id='A')
            await service.wait_idle()
            assert job.result_status=='completed' and job.cleanup_confirmed and len(model.seen)==1
            assert 'memory_stage' not in str(model.seen[0])
            assert job.overview_saved==0
            assert storage.database.connection.execute('SELECT version FROM naiyue_memories').fetchone()[0]==1
            assert not storage.memory.progress('s') or storage.memory.progress('s')[0]['accepted_turns']==0
        finally:await service.aclose()
    asyncio.run(scenario())
