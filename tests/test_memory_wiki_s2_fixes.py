"""Regression coverage for the S2 review's cross-operation boundaries."""
import asyncio
import pytest
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from test_memory_readonly import memory
from test_memory_lifecycle import source
from test_memory_wiki_mutations import args,setup,call
from memory_wiki_helpers import record,NOW


def test_confirm_obeys_current_conflict(memory):
    _,access,_=setup(memory)
    old=call(memory,args(sources=[source(access,'caller',at=1000)]))['item']
    now=1000+31*86400
    request=args(sources=[source(access,'caller',text='改用uv',at=now)])
    request['proposal']['body']='使用uv'
    call(memory,request,now=now)
    result=call(memory,dict(action='update',scope='workspace',memory_id=old['memory_id'],expected_version=1,
        change={'mode':'confirm'},sources=[source(access,'caller',text='原规则继续',at=now+1)]),now=now+1)
    assert result['item']['status']=='candidate'
    row=memory[0].database.connection.execute('SELECT gaps_json FROM naiyue_memories WHERE memory_id=?',(old['memory_id'],)).fetchone()
    assert 'known_conflict' in row[0]


def test_one_user_command_can_forget_multiple_memories(memory):
    _,access,_=setup(memory);items=[]
    for i in range(2):
        request=args(sources=[source(access,'caller',text=f'规则{i}',at=1000+i)])
        request['proposal'].update(body=f'规则{i}',subject=f'事项{i}')
        items.append(call(memory,request)['item'])
    ref=source(access,'caller',text='忘记这两条',at=2000)
    for item in items:
        assert call(memory,dict(action='forget',scope='workspace',memory_id=item['memory_id'],
            expected_version=1,sources=[ref],reason='用户要求'))['status']=='forgotten'
    with pytest.raises(MemoryReadError,match='memory_source_suppressed'):
        call(memory,args(sources=[ref]))


def test_both_directory_does_not_skip_unfetched_personal_pages(memory):
    storage,access,workspace=setup(memory)
    personal=access._personal_store.repository();personal.prepare_wiki(now=NOW)
    expected=set()
    for scope,repo,count in [('personal',personal,51),('workspace',storage.memory,1)]:
        with repo.wiki_edit(scope,workspace if scope=='workspace' else None,now=NOW,capacity=30) as writer:
            for i in range(count):
                p=WikiWriter.new_page('working_agreements',f'{i:03}','')
                writer.apply(pages=[p],memories=[record(p['page_id'],scope=scope,
                    workspace_id=workspace if scope=='workspace' else None)])
                expected.add(p['page_id'])
    access.settings=access.settings.model_copy(update={'wiki_read_tokens':16000})
    cursor=None;seen=[]
    for _ in range(20):
        result=call(memory,dict(action='list_pages',scope='both',topic='working_agreements',cursor=cursor),now=NOW)
        seen.extend(i['page_id'] for i in result['items'])
        if not result['has_more']:break
        cursor=result['next_cursor']
    assert len(seen)==len(expected) and set(seen)==expected


def test_candidate_expiry_invalidates_continuation(memory):
    storage,_,workspace=setup(memory)
    p=WikiWriter.new_page('working_agreements','候选','')
    with storage.memory.wiki_edit('workspace',workspace,now=NOW,capacity=30) as writer:
        writer.apply(pages=[p],memories=[record(p['page_id'],workspace_id=workspace,status='candidate',
            updated_at=1002,expires_at=1100),record(p['page_id'],workspace_id=workspace,status='candidate',updated_at=1001,expires_at=1005)])
    request=dict(action='list',scope='workspace',status='candidate',limit=1)
    first=call(memory,request,now=1000)
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):
        call(memory,dict(request,cursor=first['next_cursor']),now=1006)


def test_personal_chat_can_register_organization(tmp_path):
    from naiyue_test_agent.storage.project_store import ProjectStore
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    from naiyue_test_agent.application.memory.memory_jobs import MemoryJobs
    from naiyue_test_agent.memory_settings import MemorySettings
    from naiyue_test_agent.model_access import ModelCatalog
    from test_async_application import StreamingToolModel
    async def run():
        with ProjectStore(tmp_path/'chat.sqlite') as storage:
            storage.sessions.create('caller',dict(thread_id='caller',model_id='A',workspace_root=None,
                active_tool_names=['memory'],permission_mode='Default'))
            access=MemoryAccess(storage,MemorySettings(auto_enabled=False),tmp_path/'data',personal_only=True)
            repo=access._personal_store.repository();repo.prepare_wiki(now=NOW)
            p=WikiWriter.new_page('collaboration','习惯','')
            with repo.wiki_edit('personal',None,now=NOW,capacity=30) as writer:
                writer.apply(pages=[p],memories=[record(p['page_id'],scope='personal',workspace_id=None)])
            service=MemoryJobs(access,ModelCatalog('A',{'A':StreamingToolModel(replies=[])}))
            try:
                result=await service.request(source_session_id='caller',target_session_id='caller',workspace_root=None,
                    origin_run_id='r',model_id='A',wiki_request=dict(action='organize',scope='personal',page_ids=[p['page_id']]))
                assert result['status']=='accepted'
                await service.after_finish('caller','r','completed',model_id='A')
                await service.wait_idle()
                assert service._entries[result['task_id']].released
            finally:
                await service.aclose();access.close()
    asyncio.run(run())
