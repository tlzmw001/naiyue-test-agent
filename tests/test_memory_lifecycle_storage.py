"""TTL policy, transactional upgrades, contention and exact-source suppression."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest
from pydantic import ValidationError

from naiyue_test_agent.storage.memory_sql import lexical as memory_lexical
from naiyue_test_agent.storage.memory_sql import memory_schema, memory_lifecycle_schema
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.personal_database import APPLICATION_ID
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.tools.memory_wiki_actions import WikiMemoryToolInput
from test_memory_lifecycle import wiki as memory, storage_memory, source
from test_memory_wiki_mutations import call,args
from test_memory_wiki_pagination import decode_content


def format1(path):
    with closing(sqlite3.connect(path)) as c:
        memory_lexical.configure(c)
        with c:
            memory_schema.create(c)
            c.execute('PRAGMA user_version=1')
            c.execute(f'PRAGMA application_id={APPLICATION_ID}')


def test_personal_upgrade_has_closed_validated_prechange_backup(tmp_path):
    path=tmp_path/'memory.sqlite';format1(path)
    with Database.open_personal(path,MemorySettings()) as store:
        assert store.connection.execute('PRAGMA user_version').fetchone()[0]==4
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
    with pytest.raises(RuntimeError) as caught:Database.open_personal(path,MemorySettings())
    assert caught.value is expected
    with closing(sqlite3.connect(path)) as c:
        memory_schema.check(c,version=1)
        assert c.execute('PRAGMA user_version').fetchone()[0]==1
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='partial'").fetchone() is None
    assert len(list(tmp_path.glob('*.format1-*.sqlite')))==1


def test_backup_failure_prevents_upgrade(tmp_path,monkeypatch):
    from naiyue_test_agent.storage.memory_sql import memory_personal_upgrade
    path=tmp_path/'memory.sqlite';format1(path)
    def fail(*args,**kwargs):raise OSError('private snapshot error')
    monkeypatch.setattr(memory_personal_upgrade,'backup_format',fail)
    with pytest.raises(OSError):Database.open_personal(path,MemorySettings())
    with closing(sqlite3.connect(path)) as c:memory_schema.check(c,version=1)


def test_personal_two_owners_cas_only_one_commit(memory):
    storage,access,workspace,root=memory
    ref=source(access,'caller')
    request=dict(args(sources=[ref]),scope='personal',scope_source_indexes=[0])
    item=call(memory,request,now=1000)['item']
    other=MemoryAccess(storage,access.settings,root/'data')
    try:
        other.query_wiki('caller',workspace,dict(action='list',scope='personal'),now=1000)
        barrier=threading.Barrier(2)
        request=dict(action='update',scope='personal',memory_id=item['memory_id'],expected_version=1,
            change={'mode':'supplement'},sources=[ref])
        def update(owner):
            barrier.wait()
            try:return owner.query_wiki('caller',workspace,request,now=2000)['item']['version']
            except MemoryReadError as error:return str(error)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(update,[access,other]))
        assert sorted(str(r) for r in results)==['2','memory_conflict']
    finally:other.close()


@pytest.mark.parametrize('days',[30,60])
def test_configured_ttl_and_explicit_deadline(memory,days):
    _,access,_,_=memory
    access.settings=MemorySettings(default_ttl_days=days)
    ref=source(access,'caller')
    item=call(memory,args(sources=[ref]),now=1000)['item']
    assert item['ttl_days']==days and item['expires_at']==1000+days*86400
    fresh=source(access,'caller',text='规则只到指定日期有效',at=1200.)
    p=args()['proposal'];p['deadline']=1500.
    item=call(memory,dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'replace','proposal':p},sources=[fresh]),now=1200)['item']
    assert item['expires_at']==1500.


def test_changed_ttl_no_retroactive_renewal_old_quote_and_new_evidence(memory):
    storage,access,_,_=memory
    ref=source(access,'caller')
    item=call(memory,args(sources=[ref]),now=1000)['item']
    access.settings=MemorySettings(default_ttl_days=60)
    changed=args()['proposal'];changed['body']='只改写旧内容'
    request=dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'replace','proposal':changed},sources=[ref])
    with pytest.raises(MemoryReadError,match='memory_new_evidence_required'):call(memory,request,now=2000)
    row=storage.database.connection.execute('SELECT version,ttl_days,expires_at FROM naiyue_memories').fetchone()
    assert tuple(row)==(1,30,1000+30*86400)
    item=call(memory,dict(request,change={'mode':'supplement'}),now=2000)['item']
    assert item['ttl_days']==30 and item['expires_at']==1000+30*86400
    newer=source(access,'caller',text='重新确认环境',at=3000)
    item=call(memory,dict(request,expected_version=2,sources=[newer]),now=3000)['item']
    assert item['ttl_days']==60 and item['expires_at']==3000+60*86400


@pytest.mark.parametrize('settings',[{'default_ttl_days':0},{'default_ttl_days':True},
    {'default_ttl_days':36501},{'default_ttl_days':1.5},
    {'candidate_ttl_days':{'user':0}},{'active_ttl_days':{'user':30}},
    {'active_ttl_days':{'context':True}},{'candidate_ttl_days':{'other':30}}])
def test_invalid_ttl_is_rejected(settings):
    with pytest.raises(ValidationError):MemorySettings(**settings)


@pytest.mark.parametrize('changes',[{'scope':'both'},{'expected_version':1},{'status':'inactive'},
    {'proposal':None},{'sources':[]},{'include_history':True},{'version':1}])
def test_write_schema_cannot_bypass_operation(changes):
    request=args(sources=[dict(current_user=True,quote='x')])|changes
    with pytest.raises(ValidationError):WikiMemoryToolInput(request=request)


def test_tool_observation_and_inference_do_not_become_user_declarations(memory):
    from test_memory_wiki_evidence_gates import tool_source
    _,access,_,_=memory
    ref=tool_source(access,'bash')
    request=args(sources=[ref])
    with pytest.raises(MemoryReadError,match='memory_direct_source_required'):call(memory,request)
    request['proposal'].update(subject='inferred',basis='inference')
    inferred=call(memory,request)['item']
    assert inferred['status']=='candidate'
    request['proposal'].update(subject='observed',basis='tool_observation',target='local pytest')
    assert call(memory,request)['item']['status']=='active'


def test_explicit_deadline_survives_new_confirmation_without_new_deadline(memory):
    _,access,_,_=memory
    first=source(access,'caller')
    request=args(sources=[first]);request['proposal']['deadline']=5000.
    item=call(memory,request)['item']
    newer=source(access,'caller',text='确认此环境',at=2000.)
    item=call(memory,dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'confirm'},sources=[newer]),now=2000)['item']
    assert item['expires_at']==5000.


def test_source_from_different_workspace_session_is_rejected(memory,tmp_path):
    storage,access,_,root=memory
    storage.sessions.create('foreign',dict(thread_id='foreign',model_id='A',workspace_root=root,
        active_tool_names=['memory'],permission_mode='Default'))
    with storage.database.connection:
        storage.database.connection.execute('UPDATE naiyue_sessions SET project_id=? WHERE session_id=?',('foreign-project','foreign'))
    ref=source(access,'foreign')
    with pytest.raises(MemoryReadError,match='memory_source_scope_mismatch'):call(memory,args(sources=[ref]))


@pytest.mark.parametrize('separator',['\n','\t','\r\n'])
def test_sensitive_pattern_checks_original_text_not_json_escapes(memory,separator):
    storage,access,_,_=memory
    ref=source(access,'caller')
    request=args(sources=[ref]);request['proposal']['body']='password'+separator+'= SYNTHETIC_NOT_A_CREDENTIAL'
    with pytest.raises(MemoryReadError,match='memory_sensitive_content'):call(memory,request)
    assert storage.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0]==0


def test_write_response_obeys_output_budget_and_commit_is_readable(memory):
    storage,access,_,_=memory
    ref=source(access,'caller')
    access.settings=MemorySettings(max_output_bytes=4096)
    request=args(sources=[ref]);request['proposal'].update(body='字'*1000,reason='原'*300,gaps=['缺'*128]*8)
    result=call(memory,request)
    assert len(json.dumps(result,ensure_ascii=False).encode())<=4096
    assert result['status']=='still_candidate'
    assert storage.database.connection.execute('SELECT body FROM naiyue_memories').fetchone()[0]=='字'*1000
    request=dict(action='read',scope='workspace',status='candidate',memory_id=result['item']['memory_id'])
    chunks=[]
    for _ in range(30):
        page=call(memory,request)
        assert len(json.dumps(page,ensure_ascii=False).encode())<=4096
        entry,=decode_content(page)['entries'];chunks.append(entry['body'])
        if page['complete']:break
        request=dict(request,cursor=page['next_cursor'])
    assert page['complete'] and ''.join(chunks)=='字'*1000


def test_candidate_uses_same_configured_ttl_as_active(memory):
    _,access,_,_=memory
    access.settings=MemorySettings(default_ttl_days=90)
    ref=source(access,'caller')
    item=call(memory,args(sources=[ref]),now=1000)['item']
    assert item['expires_at']==1000+90*86400
    fresh=source(access,'caller',text='规则需要重新确认',at=2000.)
    p=args()['proposal'];p.update(intent='candidate',gaps=['待重确认'])
    item=call(memory,dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'replace','proposal':p},sources=[fresh]),now=2000)['item']
    assert item['status']=='candidate' and item['ttl_days']==90 and item['expires_at']==2000+90*86400


def test_orphan_thread_cannot_supply_active_tool_observation(memory):
    _,access,_,_=memory
    ref=source(access,'caller',text='pytest passed',role='tool',thread='obsolete-root')
    request=args(sources=[ref]);request['proposal'].update(basis='tool_observation',target='local pytest')
    with pytest.raises(MemoryReadError,match='memory_source_thread_unavailable'):call(memory,request)


def test_format1_existing_current_version_becomes_history_baseline(tmp_path):
    from test_memory_readonly import seed
    path=tmp_path/'memory.sqlite';format1(path)
    with closing(sqlite3.connect(path)) as c:
        memory_lexical.configure(c)
        mid=seed(c,1,scope='personal',body='OLD-ORIGINAL')
    with Database.open_personal(path,MemorySettings()) as store:
        row=store.connection.execute('SELECT snapshot_json FROM naiyue_memory_versions WHERE memory_id=? AND version=1',(mid,)).fetchone()
        assert row is not None
        assert json.loads(row[0])['body']=='OLD-ORIGINAL'


def memory_rows(database):
    """Compare every persisted projection before/after failure and after reopening."""
    return {
        'items': database.read_rows('SELECT * FROM naiyue_memories ORDER BY memory_id'),
        'versions': database.read_rows('SELECT * FROM naiyue_memory_versions ORDER BY memory_id,version'),
        'sources': database.read_rows('SELECT * FROM naiyue_memory_sources ORDER BY source_id'),
        'search': database.read_rows('SELECT rowid,* FROM naiyue_memory_search ORDER BY rowid'),
        'suppression': database.read_rows('SELECT * FROM naiyue_memory_suppression ORDER BY source_hash'),
    }


@pytest.mark.parametrize('action', ['record', 'update'])
@pytest.mark.parametrize('changed', ['revision', 'conversation_revision'])
def test_personal_stale_source_preserves_all_projections_and_can_retry(memory, action, changed):
    storage, access, project_id, root = memory
    sid = "caller"
    project = access.storage.database
    project_id = access.storage.sessions.get(sid)['project_id']
    ref = source(access, sid, text='以后测试使用.venv运行pytest')
    initial = dict(args(sources=[ref]),scope='personal',scope_source_indexes=[0])
    saved = access.query_wiki(sid, project_id, initial, now=1000.)['item']
    target = access._personal_store.database
    if action == 'update':ref=source(access,sid,text='以后测试使用.venv运行pytest',at=1500.)
    assert target is not project and target.connection is not project.connection
    assert target.path == root / 'data/personal/memory.sqlite' and target.path != project.path

    # Model the archive advancing after the caller obtained its original reference.
    with project.transaction(write=True) as connection:
        if changed == 'revision':
            connection.execute('UPDATE naiyue_chat_messages SET revision=revision+1 WHERE session_id=? AND thread_id=? AND message_id=?',
                (sid, ref['thread_id'], ref['message_id']))
        else:
            connection.execute('UPDATE naiyue_sessions SET conversation_revision=conversation_revision+1 WHERE session_id=?', (sid,))
    current_revision = project.read_rows('SELECT revision FROM naiyue_chat_messages WHERE session_id=? AND thread_id=? AND message_id=?',
        (sid, ref['thread_id'], ref['message_id']))[0]['revision']
    current_conversation = access.storage.sessions.get(sid)['conversation_revision']
    assert (current_revision, current_conversation) == ((2, 0) if changed == 'revision' else (1, 1))
    before = memory_rows(target)
    archive = project.read_rows('SELECT * FROM naiyue_chat_messages ORDER BY sequence')
    source_changes = project.connection.total_changes
    request=dict(args(sources=[ref]),scope='personal',scope_source_indexes=[0])
    request['proposal'].update(subject='new environment',body='使用.venv运行pytest')
    if action == 'update':
        request.pop('destination')
        request.update(action='update',memory_id=saved['memory_id'],expected_version=1,
            change={'mode':'replace','proposal':request.pop('proposal')})

    with pytest.raises(MemoryReadError, match='^memory_source_changed$'):
        access.query_wiki(sid, project_id, request, now=2000.)
    assert memory_rows(target) == before
    assert project.read_rows('SELECT * FROM naiyue_chat_messages ORDER BY sequence') == archive
    assert project.connection.total_changes == source_changes
    assert not target.connection.in_transaction and not target._active
    assert not project.connection.in_transaction and not project._active
    with Database.open_personal(target.path, access.settings) as reopened:
        assert memory_rows(reopened) == before

    request['sources'][0].update(revision=current_revision, conversation_revision=current_conversation)
    result = access.query_wiki(sid, project_id, request, now=2000.)
    assert result['status'] == 'saved' and result['item']['version'] == (1 if action == 'record' else 2)
    assert target.read_rows('SELECT body FROM naiyue_memories WHERE memory_id=?',(result['item']['memory_id'],))[0]['body']=='使用.venv运行pytest'
    assert project.connection.total_changes == source_changes


@pytest.mark.parametrize('action', ['record', 'update'])
def test_personal_mid_write_failure_rolls_back_every_projection_and_can_retry(memory, action, caplog):
    storage, access, project_id, root = memory
    sid = "caller"
    project = access.storage.database
    project_id = access.storage.sessions.get(sid)['project_id']
    ref = source(access, sid, text='以后测试使用.venv运行pytest')
    initial = dict(args(sources=[ref]),scope='personal',scope_source_indexes=[0])
    saved = access.query_wiki(sid, project_id, initial, now=1000.)['item']
    target = access._personal_store.database
    if action == 'update':ref=source(access,sid,text='以后测试使用.venv运行pytest',at=1500.)
    assert target is not project and target.connection is not project.connection
    assert target.path == root / 'data/personal/memory.sqlite' and target.path != project.path
    before = memory_rows(target)
    assert all(len(before[key]) == 1 for key in ('items', 'versions', 'sources', 'search'))
    assert before['suppression'] == []
    archive = project.read_rows('SELECT * FROM naiyue_chat_messages ORDER BY sequence')
    source_changes = project.connection.total_changes
    request=dict(args(sources=[ref]),scope='personal',scope_source_indexes=[0])
    request['proposal'].update(subject='new environment',body='使用.venv运行pytest')
    if action == 'update':
        request.pop('destination')
        request.update(action='update',memory_id=saved['memory_id'],expected_version=1,
            change={'mode':'replace','proposal':request.pop('proposal')})
    with target.transaction(write=True) as connection:
        connection.execute('''CREATE TRIGGER fail_personal_version BEFORE INSERT ON naiyue_memory_versions
            BEGIN SELECT RAISE(ABORT,'PRIVATE_PERSONAL_VERSION_FAULT'); END''')
    target_changes = target.connection.total_changes

    with pytest.raises(MemoryReadError, match='^memory_wiki_invalid$') as caught:
        access.query_wiki(sid, project_id, request, now=2000.)
    assert isinstance(caught.value.__cause__, sqlite3.IntegrityError)
    assert target.connection.total_changes > target_changes  # Body/FTS ran before the version insert failed.
    assert memory_rows(target) == before
    assert project.read_rows('SELECT * FROM naiyue_chat_messages ORDER BY sequence') == archive
    assert project.connection.total_changes == source_changes
    assert not target.connection.in_transaction and not target._active
    assert not project.connection.in_transaction and not project._active
    assert 'memory_wiki_group_write_failed error_type=IntegrityError' in caplog.text
    assert 'PRIVATE_PERSONAL_VERSION_FAULT' not in caplog.text and str(caught.value)=='memory_wiki_invalid'
    with Database.open_personal(target.path, access.settings) as reopened:
        assert memory_rows(reopened) == before

    with target.transaction(write=True) as connection:
        connection.execute('DROP TRIGGER fail_personal_version')
    result = access.query_wiki(sid, project_id, request, now=2000.)
    assert result['status'] == 'saved' and result['item']['version'] == (1 if action == 'record' else 2)
    assert target.read_rows('SELECT body FROM naiyue_memories WHERE memory_id=?',(result['item']['memory_id'],))[0]['body']=='使用.venv运行pytest'
    after = memory_rows(target)
    assert len(after['items']) == len(after['search']) == (2 if action == 'record' else 1)
    assert len(after['versions']) == 2
    assert len(after['sources']) == (2 if action == 'record' else 3)
    current=[r for r in after['sources'] if r['memory_id']==result['item']['memory_id'] and r['memory_version']==result['item']['version']]
    assert len(current)==(1 if action=='record' else 2)
    assert any(r['message_id']==ref['message_id'] and r['supports_scope'] for r in current)
    assert project.connection.total_changes == source_changes
    with Database.open_personal(target.path, access.settings) as reopened:
        assert memory_rows(reopened) == after
