"""Lifecycle details remain visible through current Wiki reads and budgets."""
import json
import pytest
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.memory_settings import MemorySettings
from test_memory_readonly import memory
from test_memory_lifecycle import source
from test_memory_wiki_mutations import setup,call,args
from test_memory_wiki_pagination import decode_content


def test_candidate_gaps_reason_and_deadline_can_be_read_after_write(memory):
    _,access,_=setup(memory)
    ref=source(access,'caller')
    request=args(sources=[ref])
    request['proposal'].update(intent='candidate',gaps=['缺目标环境核验'],reason='尚未实测',deadline=8000.)
    item=call(memory,request)['item']
    result=call(memory,dict(action='read',scope='workspace',status='candidate',
        memory_id=item['memory_id'],include_history=True))
    read,=decode_content(result)['entries']
    assert read['gaps']==['缺目标环境核验'] and read['reason']=='尚未实测' and read['deadline_at']==8000.
    assert read['history'][0]['gaps']==read['gaps']
    assert read['body']=='使用.venv' and result['complete']


def test_low_budget_history_can_truncate_without_hiding_current_lifecycle(memory):
    _,access,_=setup(memory)
    ref=source(access,'caller')
    request=args(sources=[ref])
    request['proposal'].update(intent='candidate',body='字'*1000,reason='因'*250,gaps=['待验证'])
    item=call(memory,request)['item']
    for version in range(1,5):
        item=call(memory,dict(action='update',scope='workspace',memory_id=item['memory_id'],
            expected_version=version,change={'mode':'supplement'},sources=[ref]))['item']
    access.settings=MemorySettings(max_output_bytes=4096)
    request=dict(action='read',scope='workspace',status='candidate',memory_id=item['memory_id'],include_history=True)
    chunks=[]
    for _ in range(30):
        result=call(memory,request)
        assert len(json.dumps(result,ensure_ascii=False).encode())<=4096
        read,=decode_content(result)['entries']
        assert read['gaps']==['待验证'] and read['reason']=='因'*250 and read['history_truncated']
        assert read['version']==5 and read['body']
        chunks.append(read['body'])
        if result['complete']:break
        request=dict(request,cursor=result['next_cursor'])
    assert result['complete'] and ''.join(chunks)=='字'*1000


def test_oversized_metadata_returns_explicit_budget_failure_without_false_complete(memory):
    storage,access,_=setup(memory)
    ref=source(access,'caller')
    access.settings=MemorySettings(max_output_bytes=4096)
    request=args(sources=[ref])
    request['proposal'].update(intent='candidate',body='x'*3000,reason='理'*300,
        gaps=['缺'*127+str(n) for n in range(8)])
    item=call(memory,request)['item']
    assert item['version']==1 and item['status']=='candidate'
    # Wiki writes return an ID receipt; oversized necessary metadata is explicitly
    # rejected by reads rather than silently truncated or falsely marked complete.
    with pytest.raises(MemoryReadError,match='memory_budget_too_small'):
        call(memory,dict(action='read',memory_id=item['memory_id'],scope='workspace',status='candidate'))
    page=call(memory,dict(action='read_page',page_id=item['page_id'],scope='workspace'))
    assert decode_content(page)['entries']==[] and page['complete']
    row=storage.database.connection.execute('SELECT body,gaps_json,reason FROM naiyue_memories').fetchone()
    assert row['body']==request['proposal']['body']
    assert json.loads(row['gaps_json'])==request['proposal']['gaps'] and row['reason']==request['proposal']['reason']
