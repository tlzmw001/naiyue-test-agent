"""Entire history projections obey their token ceiling."""
import json
from langchain_core.messages import HumanMessage,SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from test_history_tool_storage import archive,query,save
from naiyue_test_agent.tools.history_settings import HistorySettings
from naiyue_test_agent.tools.history import HistoryInput


def test_long_message_token_pages_are_lossless(archive):
    store,_=archive;text='测试正文 abc '*2000
    save(store,'target',[HumanMessage(id='h',content=text)])
    settings=HistorySettings(max_output_tokens=2000)
    offset=0;chunks=[]
    while True:
        result=query(archive,settings,action='read',session_id='target',sequence=1,revision=1,conversation_revision=0,offset=offset)
        assert count_tokens_approximately([SystemMessage(content=json.dumps(result,ensure_ascii=False))])<=2000
        item=result['items'][0];chunks.append(item['content']);offset=item['next_offset']
        if item['complete']:break
    assert ''.join(chunks)==text


def test_overview_projection_schema():
    assert HistoryInput(action='read',session_id='target',overview_only=True).overview_only


def test_overview_cursor_is_lossless_and_rejects_updated_version(archive):
    import pytest
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    store,_=archive
    save(store,'target',[HumanMessage(id='h',content='original')])
    summary=dict(goal='目标'*140,progress='进度'*140,blockers='阻碍'*140,next_step='后续'*140)
    arguments=dict(session_id='target',thread_id='target',conversation_revision=0,overview=summary,
        lower=1,upper=1,task_id='T1',origin_run_id='R',batch=1,skipped=[])
    store.memory.save_overview(**arguments)
    settings=HistorySettings(max_output_tokens=400)
    result=query(archive,settings,action='read',session_id='target',overview_only=True)
    cursor=result['next_cursor'];assert cursor and not result['overview']['complete']
    chunks=[result['overview']['content']]
    while result['has_more']:
        result=query(archive,settings,action='read',session_id='target',overview_only=True,cursor=result['next_cursor'])
        chunks.append(result['overview']['content'])
    assert json.loads(''.join(chunks))==summary
    store.memory.save_overview(**dict(arguments,batch=2,overview=dict(summary,goal='新目标')))
    with pytest.raises(SessionPersistenceError,match='history_cursor_stale'):
        query(archive,settings,action='read',session_id='target',overview_only=True,cursor=cursor)


def test_large_overview_omissions_do_not_block_normal_history(archive):
    store,root=archive
    from test_history_tool_storage import create
    from naiyue_test_agent.application.memory.memory_job_runner import _bounded_notes
    target='a'*32
    create(store,target,root)
    save(store,target,[HumanMessage(id='h',content='original')])
    store.sessions.update_presentation(target,title='标题'*20)
    store.memory.save_overview(session_id=target,thread_id=target,conversation_revision=0,
        overview={k:'摘要'*150 for k in ('goal','progress','blockers','next_step')},
        lower=1,upper=1,task_id='T'*32,origin_run_id='R'*32,batch=1,skipped=_bounded_notes([f'unprocessed_until:{i}:0:{i+1}:0' for i in range(300)]))
    for action in ('list_sessions','read'):
        result=query(archive,action=action,**({'session_id':target} if action=='read' else {}))
        assert result['status']=='ok' and result['items']
