"""TTL policy, transactional upgrades, contention and exact-source suppression."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest
from pydantic import ValidationError

from naiyue_test_agent.application.memory import memory_lexical, memory_schema, memory_lifecycle_schema
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.application.memory.memory_personal import PersonalMemory, APPLICATION_ID
from naiyue_test_agent.application.memory.memory_queries import MemoryReadError
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.tools.memory import MemoryInput
from test_memory_lifecycle import memory, storage_memory, source, proposal, write


def format1(path):
    with closing(sqlite3.connect(path)) as c:
        memory_lexical.configure(c)
        with c:
            memory_schema.create(c)
            c.execute('PRAGMA user_version=1')
            c.execute(f'PRAGMA application_id={APPLICATION_ID}')


def test_personal_upgrade_has_closed_validated_prechange_backup(tmp_path):
    path=tmp_path/'memory.sqlite';format1(path)
    with PersonalMemory(path,MemorySettings()) as store:
        assert store.connection.execute('PRAGMA user_version').fetchone()[0]==2
        assert store.connection.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        assert store.connection.execute('PRAGMA synchronous').fetchone()[0]==2
    backup,=tmp_path.glob('memory.sqlite.format1-*.sqlite')
    with closing(sqlite3.connect(backup)) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert c.execute('PRAGMA user_version').fetchone()[0]==1
        memory_schema.check(c,version=1)
        assert c.execute('SELECT name FROM sqlite_master WHERE name=?',('naiyue_memory_versions',)).fetchone() is None


def test_upgrade_failure_rolls_back_original_and_preserves_backup(tmp_path,monkeypatch):
    path=tmp_path/'memory.sqlite';format1(path)
    expected=RuntimeError('private upgrade error')
    def fail(c):
        c.execute('CREATE TABLE partial(value TEXT)')
        raise expected
    monkeypatch.setattr(memory_lifecycle_schema,'upgrade',fail)
    with pytest.raises(RuntimeError) as caught:PersonalMemory(path,MemorySettings())
    assert caught.value is expected
    with closing(sqlite3.connect(path)) as c:
        memory_schema.check(c,version=1)
        assert c.execute('PRAGMA user_version').fetchone()[0]==1
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='partial'").fetchone() is None
    assert len(list(tmp_path.glob('*.format1-*.sqlite')))==1


def test_backup_failure_prevents_upgrade(tmp_path,monkeypatch):
    from naiyue_test_agent.application.memory import memory_personal_upgrade
    path=tmp_path/'memory.sqlite';format1(path)
    def fail(*args):raise OSError('private snapshot error')
    monkeypatch.setattr(memory_personal_upgrade,'backup_format1',fail)
    with pytest.raises(OSError):PersonalMemory(path,MemorySettings())
    with closing(sqlite3.connect(path)) as c:memory_schema.check(c,version=1)


def test_personal_two_owners_cas_only_one_commit(memory):
    access,sid,root,_=memory
    ref=source(access,sid)
    request=MemoryInput(action='record',scope='personal',proposal=proposal(),sources=[ref]).model_dump(mode='json')
    item=access.query(sid,access.storage.get(sid)['project_id'],request,now=1000)['item']
    other=MemoryAccess(access.storage,access.settings,root/'data')
    try:
        other.query(sid,access.storage.get(sid)['project_id'],MemoryInput(action='list',scope='personal').model_dump(),now=1000)
        barrier=threading.Barrier(2)
        def update(owner):
            barrier.wait()
            req=MemoryInput(action='update',scope='personal',memory_id=item['memory_id'],expected_version=1,
                proposal=proposal(),sources=[ref]).model_dump(mode='json')
            try:return owner.query(sid,access.storage.get(sid)['project_id'],req,now=2000)['item']['version']
            except MemoryReadError as error:return str(error)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(update,[access,other]))
        assert sorted(str(r) for r in results)==['2','memory_conflict']
    finally:other.close()


@pytest.mark.parametrize('kind,days',[('user',None),('rule',None),('decision',None),('context',30),
    ('procedure',180),('experience',365),('reference',90)])
def test_type_ttl_and_explicit_deadline(memory,kind,days):
    access,sid,_,_=memory
    ref=source(access,sid)
    item=write(memory,proposal=proposal(kind=kind),sources=[ref])['item']
    assert item['ttl_days']==days
    assert item['expires_at']==(1000+days*86400 if days else None)
    p=proposal(kind=kind,deadline=1500.)
    item=write(memory,'update',memory_id=item['memory_id'],expected_version=1,proposal=p,sources=[ref])['item']
    assert item['expires_at']==1500.


def test_changed_ttl_no_retroactive_renewal_old_quote_and_new_evidence(memory):
    access,sid,_,_=memory
    ref=source(access,sid)
    item=write(memory,proposal=proposal(),sources=[ref])['item']
    access.settings=MemorySettings(active_ttl_days={'context':60})
    changed=proposal(body='只改写旧内容')
    item=write(memory,'update',memory_id=item['memory_id'],expected_version=1,proposal=changed,sources=[ref],now=2000)['item']
    assert item['ttl_days']==30 and item['expires_at']==1000+30*86400
    newer=source(access,sid,text='重新确认环境',at=3000)
    item=write(memory,'update',memory_id=item['memory_id'],expected_version=2,proposal=changed,sources=[newer],now=3000)['item']
    assert item['ttl_days']==60 and item['expires_at']==3000+60*86400


@pytest.mark.parametrize('settings',[{'candidate_ttl_days':{'user':0}},{'active_ttl_days':{'user':30}},
    {'active_ttl_days':{'context':True}},{'candidate_ttl_days':{'other':30}}])
def test_invalid_ttl_is_rejected(settings):
    with pytest.raises(ValidationError):MemorySettings(**settings)


@pytest.mark.parametrize('changes',[{'scope':'both'},{'expected_version':1},{'status':'inactive'},
    {'proposal':None},{'sources':[]},{'include_history':True},{'version':1}])
def test_write_schema_cannot_bypass_operation(changes):
    args=dict(action='record',scope='workspace',proposal=proposal(),sources=[dict(current_user=True,quote='x')]) | changes
    with pytest.raises(ValidationError):MemoryInput(**args)


def test_tool_observation_and_inference_do_not_become_user_declarations(memory):
    access,sid,_,_=memory
    ref=source(access,sid,text='pytest passed',role='tool')
    user=write(memory,proposal=proposal(),sources=[ref])['item']
    assert user['status']=='candidate'
    inferred=write(memory,proposal=proposal(subject='inferred',basis='inference'),sources=[ref])['item']
    assert inferred['status']=='candidate'
    observed=write(memory,proposal=proposal(subject='observed',basis='tool_observation',target='local pytest'),sources=[ref])['item']
    assert observed['status']=='active'


def test_explicit_deadline_survives_new_confirmation_without_new_deadline(memory):
    access,sid,_,_=memory
    first=source(access,sid)
    item=write(memory,proposal=proposal(deadline=5000.),sources=[first])['item']
    newer=source(access,sid,text='确认此环境',at=2000.)
    item=write(memory,'update',memory_id=item['memory_id'],expected_version=1,proposal=proposal(),sources=[newer],now=2000.)['item']
    assert item['expires_at']==5000.


def test_source_from_different_workspace_session_is_rejected(memory,tmp_path):
    access,sid,_,_=memory
    (tmp_path/'other').mkdir()
    access.storage.create('foreign',dict(thread_id='foreign',model_id='A',workspace_root=memory[2],
        active_tool_names=['memory'],permission_mode='Default'))
    # Imported/legacy rows are still checked at the source boundary.
    with access.storage.connection:
        access.storage.connection.execute('UPDATE naiyue_sessions SET project_id=? WHERE session_id=?',('foreign-project','foreign'))
    ref=source(access,'foreign')
    with pytest.raises(MemoryReadError,match='memory_source_scope_mismatch'):
        write(memory,proposal=proposal(),sources=[ref])


@pytest.mark.parametrize('separator',['\n','\t','\r\n'])
def test_sensitive_pattern_checks_original_text_not_json_escapes(memory,separator):
    access,sid,_,_=memory
    ref=source(access,sid)
    with pytest.raises(MemoryReadError,match='memory_sensitive_content'):
        write(memory,proposal=proposal(body='password'+separator+'= SYNTHETIC_NOT_A_CREDENTIAL'),sources=[ref])
    assert access.storage.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==0


def test_write_response_obeys_output_budget_and_commit_is_readable(memory):
    access,sid,_,_=memory
    ref=source(access,sid)
    access.settings=MemorySettings(max_output_bytes=4096)
    item=write(memory,proposal=proposal(body='字'*1000,reason='原'*300,gaps=['缺'*128]*8),sources=[ref])
    assert len(json.dumps(item,ensure_ascii=False).encode())<=4096
    assert item['status']=='still_candidate'
    row=access.storage.connection.execute('SELECT body FROM naiyue_memories').fetchone()
    assert row['body']=='字'*1000


def test_active_without_type_ttl_downgraded_candidate_gets_candidate_expiry(memory):
    access,sid,_,_=memory
    ref=source(access,sid)
    item=write(memory,proposal=proposal(kind='user'),sources=[ref])['item']
    assert item['expires_at'] is None
    item=write(memory,'update',memory_id=item['memory_id'],expected_version=1,
        proposal=proposal(kind='user',intent='candidate',gaps=['待重确认']),sources=[ref],now=2000.)['item']
    assert item['status']=='candidate' and item['expires_at']==1000+30*86400


def test_orphan_thread_cannot_supply_active_tool_observation(memory):
    access,sid,_,_=memory
    ref=source(access,sid,text='pytest passed',role='tool',thread='obsolete-root')
    with pytest.raises(MemoryReadError,match='memory_source_thread_unavailable'):
        write(memory,proposal=proposal(basis='tool_observation',target='local pytest'),sources=[ref])


def test_format1_existing_current_version_becomes_history_baseline(tmp_path):
    from test_memory_readonly import seed
    path=tmp_path/'memory.sqlite';format1(path)
    with closing(sqlite3.connect(path)) as c:
        memory_lexical.configure(c)
        mid=seed(c,1,scope='personal',body='OLD-ORIGINAL')
    with PersonalMemory(path,MemorySettings()) as store:
        row=store.connection.execute('SELECT snapshot_json FROM naiyue_memory_versions WHERE memory_id=? AND version=1',(mid,)).fetchone()
        assert row is not None
        assert json.loads(row[0])['body']=='OLD-ORIGINAL'
