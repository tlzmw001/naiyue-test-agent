"""Lifecycle details remain visible after losing the original write receipt."""

import json

from test_memory_lifecycle import memory, storage_memory, source, proposal, write, query


def test_candidate_gaps_reason_and_deadline_can_be_read_after_write(memory):
    access,sid,root,_=memory
    ref=source(access,sid)
    p=proposal(intent='candidate',gaps=['缺目标环境核验'],reason='尚未实测',deadline=8000.)
    item=write(memory,proposal=p,sources=[ref])['item']
    read=query(access,sid,root,action='read',scope='workspace',status='candidate',memory_id=item['memory_id'],include_history=True)['items'][0]
    assert read['gaps']==['缺目标环境核验'] and read['reason']=='尚未实测' and read['deadline_at']==8000.
    assert read['history'][0]['gaps']==read['gaps']


def test_low_budget_history_can_truncate_without_hiding_current_lifecycle(memory):
    from naiyue_test_agent.memory_settings import MemorySettings
    access,sid,root,_=memory
    ref=source(access,sid)
    p=proposal(intent='candidate',body='字'*1000,reason='因'*250,gaps=['待验证'])
    item=write(memory,proposal=p,sources=[ref])['item']
    for version in range(1,5):
        item=write(memory,'update',memory_id=item['memory_id'],expected_version=version,proposal=p,sources=[ref])['item']
    access.settings=MemorySettings(max_output_bytes=4096)
    read=query(access,sid,root,action='read',scope='workspace',status='candidate',memory_id=item['memory_id'],include_history=True)
    assert len(json.dumps(read,ensure_ascii=False).encode())<=4096
    assert read['items'][0]['gaps']==['待验证'] and read['items'][0]['history_truncated']


def test_write_rejects_unreadable_metadata_before_committing(memory):
    import pytest
    from naiyue_test_agent.application.memory.memory_queries import MemoryReadError
    from naiyue_test_agent.memory_settings import MemorySettings
    access,sid,_,_=memory
    ref=source(access,sid)
    access.settings=MemorySettings(max_output_bytes=4096)
    p=proposal(intent='candidate',body='x'*3000,reason='理'*300,gaps=['缺'*127+str(n) for n in range(8)])
    with pytest.raises(MemoryReadError,match='memory_metadata_budget'):
        write(memory,proposal=p,sources=[ref])
    for table in ('naiyue_memories','naiyue_memory_versions','naiyue_memory_sources','naiyue_memory_search'):
        assert access.storage.connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
