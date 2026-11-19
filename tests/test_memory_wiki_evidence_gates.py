"""Real producer/provenance and clock gates; no semantic keyword classification."""
import hashlib
import json
from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage,ToolMessage,message_to_dict
from naiyue_test_agent.application.conversation.chat_history import ChatUpdate
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_readonly import memory
from test_memory_lifecycle import source
from test_memory_wiki_mutations import setup,args,call


def tool_source(access,producer,at=2000,status='success'):
    store=access.storage;cid=uuid4().hex
    ai=AIMessage(content='',id=uuid4().hex,tool_calls=[dict(id=cid,name=producer,args={})])
    tool=ToolMessage(content='观测结果',id=uuid4().hex,name=producer,tool_call_id=cid,status=status)
    heads=store.conversation.chat_heads('caller');head=next((h for h in heads if h['thread_id']=='caller'),None)
    records=[]
    for m in (ai,tool):
        text=json.dumps(message_to_dict(m),ensure_ascii=False)
        records.append(dict(message_id=m.id,body_json=text,body_hash=hashlib.sha256(text.encode()).hexdigest(),kind='message',
            source_checkpoint_id='checkpoint',message_type=m.type,parent_message_id=ai.id if m is tool else None,
            reply_id='reply',occurred_at=at))
    store.conversation.save_chat_updates('caller',[ChatUpdate('caller','','root',head['revision'] if head else 0,'checkpoint',tuple(records))])
    return dict(session_id='caller',thread_id='caller',message_id=tool.id,revision=1,conversation_revision=0,quote='观测结果',offset=0)


@pytest.mark.parametrize('producer',['memory','history'])
def test_self_reference_is_rejected_even_as_candidate(memory,producer):
    storage,access,workspace=setup(memory)
    ref=tool_source(access,producer)
    request=args(sources=[ref]);request['proposal'].update(basis='tool_observation',target='.venv',intent='candidate')
    with pytest.raises(MemoryReadError,match='memory_self_reference'):call(memory,request)
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0


def test_paired_successful_tool_may_support_workspace_but_cannot_prove_personal_scope(memory):
    storage,access,workspace=setup(memory)
    ref=tool_source(access,'bash')
    request=args(sources=[ref]);request['proposal'].update(basis='tool_observation',target='.venv')
    saved=call(memory,request)['item'];assert saved['status']=='active'
    access._personal_store.repository().prepare_wiki(now=4000)
    with pytest.raises(MemoryReadError,match='memory_scope_evidence_required'):
        call(memory,dict(request,scope='personal',scope_source_indexes=[0]))


def test_failed_or_unpaired_tool_does_not_fall_back_to_candidate(memory):
    storage,access,workspace=setup(memory)
    for ref in (tool_source(access,'bash',status='error'),source(access,'caller',role='tool',at=2100)):
        request=args(sources=[ref]);request['proposal'].update(basis='tool_observation',target='.venv',intent='candidate')
        with pytest.raises(MemoryReadError,match='memory_direct_source_required|memory_tool_source_unpaired'):
            call(memory,request)
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0


def test_expired_confirm_requires_real_new_source_deleted_id_is_not_recreated(memory):
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000);item=call(memory,args(sources=[ref]))['item']
    now=item['expires_at']+1
    fresh=source(access,'caller',text='完整规则再次确认',at=now)
    request=dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'confirm'},sources=[fresh])
    result=call(memory,request,now=now)['item']
    assert result['expires_at']==now+30*86400
    with storage.database.transaction(write=True) as c:c.execute('DELETE FROM naiyue_memories WHERE memory_id=?',(item['memory_id'],))
    with pytest.raises(MemoryReadError,match='memory_not_found'):call(memory,dict(request,expected_version=2),now=now)
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0


def test_supplement_cannot_drop_necessary_scope_or_body_sources_to_fit(memory):
    storage,access,workspace=setup(memory)
    refs=[source(access,'caller',text=f'依据{i}',at=1000+i) for i in range(8)]
    item=call(memory,args(sources=refs))['item']
    ref=source(access,'caller',text='补充来源',at=2000)
    with pytest.raises(MemoryReadError,match='memory_source_limit'):
        call(memory,dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
            change={'mode':'supplement'},sources=[ref]))
    assert storage.database.connection.execute('SELECT version FROM naiyue_memories').fetchone()[0]==1


def test_page_metadata_cannot_bypass_secret_filter(memory):
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=1000)
    request=args(sources=[ref]);request['destination']['new_page']['description']='password=private-value'
    with pytest.raises(MemoryReadError,match='memory_sensitive_content'):call(memory,request)
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memory_pages').fetchone()[0]==0
