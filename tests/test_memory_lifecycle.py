"""Explicit writes against real archives and two SQLite stores; no model/network."""

import json
import sqlite3
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage, ToolMessage, message_to_dict

from naiyue_test_agent.application.chat_history import ChatUpdate
from naiyue_test_agent.application.memory_queries import MemoryReadError
from naiyue_test_agent.tools.memory import MemoryInput
from test_memory_readonly import memory as storage_memory


@pytest.fixture
def memory(storage_memory):
    _, access, _, root = storage_memory
    return access, "caller", root, None


def query(access, sid, root, **arguments):
    return access.query(sid, root, MemoryInput(**arguments).model_dump(mode="json"), now=4000.)


def source(access, sid, *, text='以后测试使用.venv', at=1000., role='human', thread=None):
    storage = access.storage
    parent = storage.get(sid)
    thread = thread or parent['thread_id']
    message = (HumanMessage(content=text, id=uuid4().hex, additional_kwargs={'naiyue_human_input':True})
        if role == 'human' else ToolMessage(content=text, id=uuid4().hex, tool_call_id=uuid4().hex, status='success'))
    body = json.dumps(message_to_dict(message),ensure_ascii=False)
    import hashlib
    heads = {h['thread_id']:h for h in storage.chat_heads(sid)}
    update = ChatUpdate(thread,'','root' if thread==parent['thread_id'] else 'tester',
        heads.get(thread,{}).get('revision',0),uuid4().hex,tuple([dict(message_id=message.id,
        body_json=body,body_hash=hashlib.sha256(body.encode()).hexdigest(),kind='message',
        source_checkpoint_id=uuid4().hex,message_type=role,parent_message_id=None,reply_id=None,occurred_at=at)]))
    storage.save_chat_updates(sid,[update])
    return dict(session_id=sid,thread_id=thread,message_id=message.id,revision=1,
        conversation_revision=parent['conversation_revision'],quote=text,offset=0)


def proposal(**changes):
    return dict(kind='context',title='测试环境',body='使用.venv',subject='pytest环境',
        basis='user_declaration',intent='activate',reason='用户明确长期约定') | changes


def write(fixture, action='record', *, now=1000., **args):
    access,sid,root,_ = fixture
    request = MemoryInput(action=action,scope='workspace',**args).model_dump(mode='json')
    return access.query(sid,root,request,now=now)


def test_record_update_cas_and_history(memory):
    access,sid,root,_=memory
    ref=source(access,sid)
    result=write(memory,proposal=proposal(),sources=[ref])
    assert result['status']=='saved' and result['item']['status']=='active'
    item=result['item']; mid=item['memory_id']
    changed=proposal();changed['body']='使用.venv运行pytest'
    update=write(memory,'update',memory_id=mid,expected_version=1,proposal=changed,sources=[ref],now=2000.)
    assert update['item']['version']==2 and update['item']['expires_at']==1000+30*86400
    with pytest.raises(MemoryReadError,match='memory_conflict'):
        write(memory,'update',memory_id=mid,expected_version=1,proposal=changed,sources=[ref])
    read=query(access,sid,root,action='read',scope='workspace',memory_id=mid,include_history=True)
    assert [v['version'] for v in read['items'][0]['history']]==[2,1]


def test_candidate_requires_real_new_evidence_for_promotion_and_renewal(memory):
    access,sid,_,_=memory
    ref=source(access,sid,at=1000)
    p=proposal();p.update(intent='candidate',gaps=['待确认'])
    item=write(memory,proposal=p,sources=[ref])['item']
    assert item['status']=='candidate'
    p.update(intent='activate',gaps=[])
    still=write(memory,'update',memory_id=item['memory_id'],expected_version=1,proposal=p,sources=[ref])
    assert still['status']=='still_candidate'
    new=source(access,sid,text='确认以后一直使用.venv',at=3000)
    active=write(memory,'update',memory_id=item['memory_id'],expected_version=2,proposal=p,sources=[new],now=3000)['item']
    assert active['status']=='active' and active['evidence_at']==3000 and active['expires_at']==3000+30*86400


def test_forget_erases_every_projection_and_blocks_old_source(memory):
    access,sid,root,_=memory
    ref=source(access,sid)
    item=write(memory,proposal=proposal(),sources=[ref])['item']
    mid=item['memory_id']
    write(memory,'forget',memory_id=mid,expected_version=1,sources=[ref],reason='用户要求遗忘')
    conn=access.storage.connection
    assert conn.execute('SELECT body FROM naiyue_memories WHERE memory_id=?',(mid,)).fetchone()[0]==''
    for table in ('naiyue_memory_versions','naiyue_memory_sources','naiyue_memory_search'):
        assert conn.execute(f'SELECT count(*) FROM {table} WHERE memory_id=?',(mid,)).fetchone()[0]==0
    assert query(access,sid,root,action='search',scope='workspace',query='.venv')['items']==[]
    with pytest.raises(MemoryReadError,match='memory_source_suppressed'):
        write(memory,proposal=proposal(),sources=[ref])
    with pytest.raises(MemoryReadError,match='memory_conflict'):
        write(memory,'update',memory_id=mid,expected_version=1,proposal=proposal(),sources=[ref])


@pytest.mark.parametrize('failure',['stale','quote','child','secret'])
def test_untrusted_evidence_never_activates_or_leaks(memory,failure):
    access,sid,_,_=memory
    ref=source(access,sid,thread='subagent:child' if failure=='child' else None)
    if failure=='stale':ref['revision']=2
    if failure=='quote':ref['quote']='不在原文'
    p=proposal()
    if failure=='secret':p['body']='Authorization: Bearer private-secret-value'
    if failure in ('stale','quote','secret','child'):
        with pytest.raises(MemoryReadError):write(memory,proposal=p,sources=[ref])
        assert access.storage.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==0


def test_disable_conflict_and_transaction_rollback(memory,monkeypatch):
    access,sid,_,_=memory
    ref=source(access,sid)
    item=write(memory,proposal=proposal(),sources=[ref])['item']
    p=proposal();p['body']='使用另一个环境'
    other=write(memory,proposal=p,sources=[ref])['item']
    assert other['status']=='candidate' and 'known_conflict' in other['gaps']
    write(memory,'disable',memory_id=item['memory_id'],expected_version=1,sources=[ref],reason='暂时停用')
    assert query(access,sid,memory[2],action='list',scope='workspace')['items']==[]
    conn=access.storage.connection
    conn.execute("CREATE TRIGGER fail_memory_version BEFORE INSERT ON naiyue_memory_versions BEGIN SELECT RAISE(ABORT,'private fault');END")
    with pytest.raises(MemoryReadError,match='memory_write_failed'):
        write(memory,proposal=proposal(subject='new'),sources=[ref])
    assert conn.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==2
