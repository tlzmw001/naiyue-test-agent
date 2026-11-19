"""Explicit writes against real archives and two SQLite stores; no model/network."""

import json
import sqlite3
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage, ToolMessage, message_to_dict

from naiyue_test_agent.application.conversation.chat_history import ChatUpdate
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.tools.memory import MemoryInput
from test_memory_readonly import memory as storage_memory


@pytest.fixture
def memory(storage_memory):
    _, access, _, root = storage_memory
    return access, "caller", root, None


def query(access, sid, root, **arguments):
    return access.query(sid, access.storage.sessions.get(sid)['project_id'], MemoryInput(**arguments).model_dump(mode="json"), now=4000.)


def source(access, sid, *, text='以后测试使用.venv', at=1000., role='human', thread=None):
    storage = access.storage
    parent = storage.sessions.get(sid)
    thread = thread or parent['thread_id']
    message = (HumanMessage(content=text, id=uuid4().hex, additional_kwargs={'naiyue_human_input':True})
        if role == 'human' else ToolMessage(content=text, id=uuid4().hex, tool_call_id=uuid4().hex, status='success'))
    body = json.dumps(message_to_dict(message),ensure_ascii=False)
    import hashlib
    heads = {h['thread_id']:h for h in storage.conversation.chat_heads(sid)}
    update = ChatUpdate(thread,'','root' if thread==parent['thread_id'] else 'tester',
        heads.get(thread,{}).get('revision',0),uuid4().hex,tuple([dict(message_id=message.id,
        body_json=body,body_hash=hashlib.sha256(body.encode()).hexdigest(),kind='message',
        source_checkpoint_id=uuid4().hex,message_type=role,parent_message_id=None,reply_id=None,occurred_at=at)]))
    storage.conversation.save_chat_updates(sid,[update])
    return dict(session_id=sid,thread_id=thread,message_id=message.id,revision=1,
        conversation_revision=parent['conversation_revision'],quote=text,offset=0)


def proposal(**changes):
    return dict(kind='context',title='测试环境',body='使用.venv',subject='pytest环境',
        basis='user_declaration',intent='activate',reason='用户明确长期约定') | changes


def write(fixture, action='record', *, now=1000., **args):
    access,sid,root,_ = fixture
    request = MemoryInput(action=action,scope='workspace',**args).model_dump(mode='json')
    return access.query(sid,access.storage.sessions.get(sid)['project_id'],request,now=now)


@pytest.fixture
def wiki(storage_memory):
    storage_memory[0].memory.prepare_wiki(now=1000.)
    return storage_memory


def test_record_update_cas_and_history(wiki):
    from test_memory_wiki_mutations import call,args
    from test_memory_wiki_pagination import decode_content
    _,access,_,_=wiki
    ref=source(access,'caller')
    result=call(wiki,args(sources=[ref]))
    assert result['status']=='saved' and result['item']['status']=='active'
    item=result['item'];mid=item['memory_id']
    changed=args()['proposal'];changed['body']='使用.venv运行pytest'
    newer=source(access,'caller',text='以后使用.venv运行pytest',at=2000.)
    request=dict(action='update',scope='workspace',memory_id=mid,expected_version=1,
        change={'mode':'replace','proposal':changed},sources=[newer])
    update=call(wiki,request)
    assert update['item']['version']==2 and update['item']['expires_at']==2000+30*86400
    with pytest.raises(MemoryReadError,match='memory_conflict'):call(wiki,request)
    read,=decode_content(call(wiki,dict(action='read',scope='workspace',memory_id=mid,include_history=True)))['entries']
    assert [v['version'] for v in read['history']]==[2,1]
    assert read['body']=='使用.venv运行pytest'


def test_candidate_requires_real_new_evidence_for_promotion_and_renewal(wiki):
    from test_memory_wiki_mutations import call,args
    storage,access,_,_=wiki
    ref=source(access,'caller',at=1000)
    request=args(sources=[ref]);request['proposal'].update(intent='candidate',gaps=['待确认'])
    item=call(wiki,request)['item']
    assert item['status']=='candidate'
    request['proposal'].update(intent='activate',gaps=[])
    update=dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'replace','proposal':request['proposal']},sources=[ref])
    with pytest.raises(MemoryReadError,match='memory_new_evidence_required'):call(wiki,update)
    assert tuple(storage.database.connection.execute('SELECT version,status,evidence_at FROM naiyue_memories').fetchone())==(1,'candidate',1000.)
    new=source(access,'caller',text='确认以后一直使用.venv',at=3000)
    active=call(wiki,dict(update,sources=[new]),now=3000)['item']
    assert active['version']==2 and active['status']=='active'
    assert active['evidence_at']==3000 and active['expires_at']==3000+30*86400


def test_forget_erases_every_projection_and_blocks_old_source(wiki):
    from test_memory_wiki_mutations import call,args
    storage,access,_,_=wiki
    ref=source(access,'caller')
    item=call(wiki,args(sources=[ref]))['item'];mid=item['memory_id']
    call(wiki,dict(action='forget',scope='workspace',memory_id=mid,expected_version=1,sources=[ref],reason='用户要求遗忘'))
    conn=storage.database.connection
    assert conn.execute('SELECT body FROM naiyue_memories WHERE memory_id=?',(mid,)).fetchone()[0]==''
    for table in ('naiyue_memory_versions','naiyue_memory_sources','naiyue_memory_search'):
        assert conn.execute(f'SELECT count(*) FROM {table} WHERE memory_id=?',(mid,)).fetchone()[0]==0
    assert call(wiki,dict(action='search',scope='workspace',query='.venv'))['items']==[]
    with pytest.raises(MemoryReadError,match='memory_source_suppressed'):call(wiki,args(sources=[ref]))
    with pytest.raises(MemoryReadError,match='memory_conflict'):
        call(wiki,dict(action='update',scope='workspace',memory_id=mid,expected_version=1,
            change={'mode':'replace','proposal':args()['proposal']},sources=[ref]))


@pytest.mark.parametrize('failure',['stale','quote','child','secret'])
def test_untrusted_evidence_never_activates_or_leaks(wiki,failure):
    from test_memory_wiki_mutations import call,args
    storage,access,_,_=wiki
    ref=source(access,'caller',thread='subagent:child' if failure=='child' else None)
    if failure=='stale':ref['revision']=2
    if failure=='quote':ref['quote']='不在原文'
    request=args(sources=[ref])
    if failure=='secret':request['proposal']['body']='Authorization: Bearer private-secret-value'
    with pytest.raises(MemoryReadError):call(wiki,request)
    assert storage.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==0
    assert storage.database.connection.execute('SELECT count(*) FROM naiyue_memory_pages').fetchone()[0]==0


def test_disable_conflict_and_transaction_rollback(wiki,monkeypatch):
    from test_memory_wiki_mutations import call,args
    from test_memory_wiki_pagination import decode_content
    storage,access,_,_=wiki
    ref=source(access,'caller')
    item=call(wiki,args(sources=[ref]))['item']
    request=args(sources=[ref]);request['proposal']['body']='使用另一个环境'
    other=call(wiki,request)['item']
    read,=decode_content(call(wiki,dict(action='read',scope='workspace',status='candidate',memory_id=other['memory_id'])))['entries']
    assert other['status']=='candidate' and 'known_conflict' in read['gaps']
    call(wiki,dict(action='disable',scope='workspace',memory_id=item['memory_id'],expected_version=1,sources=[ref],reason='暂时停用'))
    assert call(wiki,dict(action='list',scope='workspace'))['items']==[]
    conn=storage.database.connection
    conn.execute("CREATE TRIGGER fail_memory_version BEFORE INSERT ON naiyue_memory_versions BEGIN SELECT RAISE(ABORT,'private fault');END")
    request=args(sources=[ref]);request['proposal']['subject']='new'
    with pytest.raises(MemoryReadError,match='memory_wiki_invalid'):call(wiki,request)
    assert conn.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==2
    assert conn.execute('SELECT count(*) FROM naiyue_memory_pages').fetchone()[0]==2
