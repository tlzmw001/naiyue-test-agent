"""S06.1a archive behavior, independent of a running task container."""
import json
from hashlib import sha256
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, message_to_dict

from naiyue_test_agent.contracts.cloud_events import CloudEvent, EventBatch
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.cloud.history import CloudHistory


def record(message, *, position=1, revision=1, thread='root-thread', role='root', parent=None):
    body=json.dumps(message_to_dict(message),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return dict(record='message',version=2,command_id=str(uuid4()),thread_id=thread,
        thread=dict(thread_id=thread,agent_path='root' if role=='root' else 'root/test/child',role=role,
            agent_id=None if role=='root' else 'child',parent_run_id=None if role=='root' else 'a'*32,
            parent_tool_call_id=None if role=='root' else 'delegate',parent_reply_id=None),
        checkpoint_id='checkpoint',boundary=None,message=dict(message_id=message.id,body_json=body,
            body_hash=sha256(body.encode()).hexdigest(),message_type=message.type,parent_message_id=parent,
            reply_id=None,kind='message',source_checkpoint_id='checkpoint',occurred_at=1.0,
            revision=revision,position=position))


def append(store, payloads, *, start=1, epoch='10000000-0000-0000-0000-000000000001', inputs=None):
    batch=EventBatch(task_id='b'*32,generation=1,epoch=epoch,events=[CloudEvent(seq=i,
        source_generation=1,run_id='a'*32,kind='history_record',payload=p) for i,p in enumerate(payloads,start)])
    return store.append(batch,owner_inputs=inputs or {})


def page(store, **kwargs):
    return store.archive_page(thread_id='root-thread',limit=2,read_timeout=5.0,**kwargs)


def test_revision_pages_keep_fixed_coverage_and_reopen_without_runner(tmp_path):
    path=tmp_path/'history.sqlite'
    with Database.open_cloud(path,profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True)
        payloads=[record(AIMessage(id=str(i),content='old-'+str(i)),position=i) for i in (1,2,3)]
        assert append(store,payloads)['last_seq']==3
        first=page(store)
        assert [r['message_id'] for r in first['messages']]==['2','3']
        revised=record(AIMessage(id='1',content='new'),position=1,revision=2)
        append(store,[revised],start=4)
        old=page(store,upper=first['coverage_seq'],before=first['next_before'])
        assert old['messages'][0]['revision']==1
        detail=store.archive_message(thread_id='root-thread',message_id='1',revision=old['messages'][0]['revision'],read_timeout=5)
        assert json.loads(detail['body_json'])['data']['content']=='old-1'
        assert page(store,before=2)['messages'][0]['revision']==2
        # A lost ACK does not create another version or advance beyond the journal.
        assert append(store,[revised],start=4)['last_seq']==4
    with Database.open_cloud(path,profile='history',initialize=False) as db:
        store=CloudHistory(db,'b'*32,initialize=False)
        assert page(store,before=2)['messages'][0]['revision']==2
        assert store.rebuild()['last_seq']==4
        assert page(store,before=2)['messages'][0]['revision']==2


def test_large_body_is_invisible_until_verified_commit_and_blocks_terminal(tmp_path):
    from naiyue_test_agent.contracts.cloud_history import HistoryLimits, message_frames
    limits=HistoryLimits(max_message_bytes=100000,max_pending_bytes=200000)
    with Database.open_cloud(tmp_path/'history.sqlite',profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True,limits=limits)
        original=record(AIMessage(id='big',content='中\\\"'*5000))
        frames=list(message_frames(original,frame_bytes=4096,limits=limits))
        assert len(frames)>3 and frames[-1]['record']=='message_commit'
        assert all(len(json.dumps(p,ensure_ascii=False).encode())<3072 for p in frames)
        append(store,frames[:1]); assert page(store)['messages']==[]
        terminal=dict(record='run_finished',command_id=original['command_id'],status='completed',session_state='ready')
        with pytest.raises(CloudError,match='history_parts_pending'):append(store,[terminal],start=2)
        assert store.page(limit=1,byte_limit=4096,read_timeout=5)['last_seq']==1
        broken=[dict(p) for p in frames[1:]]; broken[-2]=broken[-2]|{'text':'corrupt'}
        with pytest.raises(CloudError):append(store,broken,start=2)
        assert page(store)['messages']==[]
        append(store,frames[1:]+[terminal],start=2)
        detail=store.archive_message(thread_id='root-thread',message_id='big',revision=1,read_timeout=5)
        assert detail['body_json']==original['message']['body_json']


def test_pairing_thread_ownership_and_untrusted_human_origin(tmp_path):
    with Database.open_cloud(tmp_path/'history.sqlite',profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True)
        user=record(HumanMessage(id='u',content='owner request'))
        append(store,[user],inputs={user['command_id']:dict(run_id='a'*32,message='owner request')})
        fake=record(HumanMessage(id='fake',content='not submitted'),position=2)
        append(store,[fake],start=2)
        assert [r['owner_input'] for r in page(store)['messages']]==[True,False]
        ai=record(AIMessage(id='call',content='',tool_calls=[dict(id='tool1',name='read',args={})]),position=3)
        tool=record(ToolMessage(id='tool',content='ok',tool_call_id='tool1'),position=4,parent='call')
        with pytest.raises(CloudError,match='history_parent_missing'):append(store,[tool],start=3)
        append(store,[ai,tool],start=3)
        child=record(AIMessage(id='answer',content='child result'),thread='child-thread',role='child')
        child['thread']['parent_tool_call_id']='tool1'
        append(store,[child],start=5)
        threads=store.archive_threads(limit=10,read_timeout=5)
        assert {r['thread_id'] for r in threads['threads']}=={'root-thread','child-thread'}
        assert next(r for r in threads['threads'] if r['role']=='child')['parent_tool_call_id']=='tool1'
        bad=record(AIMessage(id='call',content='bad revision'),position=3,revision=3)
        with pytest.raises(CloudError,match='history_revision_conflict'):append(store,[bad],start=6)
        assert store.page(limit=10,byte_limit=100000,read_timeout=5)['last_seq']==5


@pytest.mark.parametrize('damage',['hash','type','position','thread'])
def test_invalid_projection_is_atomic_with_cursor(tmp_path,damage):
    with Database.open_cloud(tmp_path/'history.sqlite',profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True)
        payload=record(AIMessage(id='a',content='safe'))
        if damage=='hash':payload['message']['body_hash']='0'*64
        if damage=='type':payload['message']['message_type']='system'
        if damage=='position':payload['message']['position']=0
        if damage=='thread':payload['thread']['thread_id']='different'
        with pytest.raises(CloudError):append(store,[payload])
        assert page(store)['coverage_seq']==0 and page(store)['messages']==[]
