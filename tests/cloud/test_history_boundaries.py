"""Malformed task data cannot escape the archive boundary or acknowledge partial state."""
import json
import sqlite3
from hashlib import sha256

import pytest
from langchain_core.messages import AIMessage,ToolMessage

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud.schema import SCHEMAS,APPLICATION_IDS
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.cloud.history import CloudHistory
from test_history_projection import record,append,page


@pytest.mark.parametrize('damage',['data_list','huge_position','huge_revision','duplicate_body_key','deep_body','part_id_object'])
def test_malformed_task_record_is_a_typed_error_and_rolls_back(tmp_path,damage):
    with Database.open_cloud(tmp_path/'history.sqlite',profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True);payload=record(AIMessage(id='a',content='ok'))
        if damage in ('data_list','duplicate_body_key','deep_body'):
            body={'data_list':'{"type":"ai","data":[]}',
                'duplicate_body_key':'{"type":"ai","type":"human","data":{}}',
                'deep_body':'{"type":"ai","data":'+('['*200)+'0'+(']'*200)+'}'}[damage]
            payload['message'].update(body_json=body,body_hash=sha256(body.encode()).hexdigest())
        elif damage=='huge_position':payload['message']['position']=2**80
        elif damage=='huge_revision':payload['message']['revision']=2**80
        else:payload=dict(record='message_part',version=2,transfer_id={},index=1,text='x')
        with pytest.raises(CloudError):append(store,[payload])
        assert page(store)['coverage_seq']==0


def test_tool_preview_preserves_pair_status_and_uses_verified_reply(tmp_path):
    with Database.open_cloud(tmp_path/'history.sqlite',profile='history',initialize=True) as db:
        store=CloudHistory(db,'b'*32,initialize=True)
        ai=record(AIMessage(id='a',content='',tool_calls=[dict(id=i,name='read',args={}) for i in ('call1','call2')]))
        tools=[record(ToolMessage(id='t'+str(i),content='output',tool_call_id=call_id,name='read',status=status,
            additional_kwargs={'naiyue_reply_id':'x'*10000}),position=i+2,parent='a')
            for i,(call_id,status) in enumerate((('call1','success'),('call2','error')))]
        append(store,[ai,*tools])
        rows=store.archive_page(thread_id='root-thread',limit=3,read_timeout=5)['messages']
        assert [(r['preview']['tool_call_id'],r['preview']['status']) for r in rows[1:]]==[('call1','success'),('call2','error')]
        assert all(r['preview']['reply_id'] is None for r in rows)
        assert len(json.dumps(rows))<10000


def test_v1_migration_keeps_journal_and_accepts_proven_new_revision(tmp_path):
    path=tmp_path/'history.sqlite'
    legacy=record(AIMessage(id='old',content='legacy'))
    legacy.pop('version');legacy.pop('thread');legacy.pop('boundary')
    legacy['message'].pop('revision');legacy['message'].pop('position')
    from naiyue_test_agent.contracts.cloud_events import canonical_json,digest,CloudEvent
    event=CloudEvent(seq=1,source_generation=1,run_id='a'*32,kind='history_record',payload=legacy)
    c=sqlite3.connect(path)
    for sql in SCHEMAS['history']:c.execute(sql)
    c.execute('PRAGMA application_id='+str(APPLICATION_IDS['history']));c.execute('PRAGMA user_version=1')
    c.executemany('INSERT INTO meta VALUES(?,?)',[('task_id','b'*32),('last_seq','1'),('epoch','10000000-0000-0000-0000-000000000001')])
    c.execute('INSERT INTO events VALUES(1,1,?,?,?,?,?)',('a'*32,'history_record',canonical_json(legacy),digest(event.model_dump()),1.0))
    c.commit();c.close();path.chmod(0o600)
    with Database.open_cloud(path,profile='history',initialize=False) as db:
        store=CloudHistory(db,'b'*32,initialize=False)
        assert page(store)['metadata_incomplete'] and page(store)['messages']==[]
        append(store,[record(AIMessage(id='old',content='new'),revision=2)],start=2)
        assert page(store)['messages'][0]['revision']==2
        assert store.page(limit=10,byte_limit=100000,read_timeout=5)['events'][0]['payload']==legacy
        assert store.rebuild()['last_seq']==2
