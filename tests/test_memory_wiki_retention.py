"""Bounded Wiki expiry GC on disposable real SQLite stores."""
import pytest
from test_memory_wiki_storage import store,page,save
from test_memory_readonly import memory

from memory_wiki_helpers import record,NOW


def test_expiry_grace_boundary_and_empty_page_cleanup(store):
    p=page();m=record(p['page_id'],expires_at=NOW+10)
    save(store,[p],[m])
    before=store.memory.prune_expired(now=NOW+10+7*86400-1,grace_days=7,batch_size=2)
    assert before['deleted_memories']==0
    result=store.memory.prune_expired(now=NOW+10+7*86400,grace_days=7,batch_size=2)
    assert result['deleted_memories']==1 and result['deleted_sources']==1 and result['deleted_versions']==1
    assert result['deleted_pages']==1 and result['reason']=='pruned'
    assert store.database.connection.execute('SELECT COUNT(*) FROM naiyue_memory_search').fetchone()[0]==0


def test_many_versions_progress_is_bounded_and_forgotten_is_retained(store):
    p=page();m=record(p['page_id'],expires_at=NOW+10);save(store,[p],[m])
    for version in range(2,8):
        row=dict(m['row'],version=version)
        save(store,[dict(p,expected_version=version-1)], [dict(row=row,sources=m['sources'],expected_version=version-1)])
    result=store.memory.prune_expired(now=NOW+20,grace_days=0,batch_size=2)
    assert result['deleted_versions']==2 and result['deleted_memories']==0 and result['more_work']
    for _ in range(5):result=store.memory.prune_expired(now=NOW+20,grace_days=0,batch_size=2)
    assert store.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0


def test_newer_confirmation_is_not_expired(store):
    p=page();m=record(p['page_id'],expires_at=NOW+1000000);save(store,[p],[m])
    result=store.memory.prune_expired(now=NOW+20,grace_days=0,batch_size=1)
    assert result['deleted_memories']==0


def test_cleanup_config_defaults_and_validation():
    from naiyue_test_agent.memory_settings import MemorySettings
    from pydantic import ValidationError
    assert MemorySettings().expiry_grace_days==7
    assert MemorySettings(cleanup_interval_seconds=0).cleanup_interval_seconds==0
    with pytest.raises(ValidationError):MemorySettings(cleanup_batch_size=0)


def test_open_personal_is_not_created_and_failed_domain_is_independent(memory,monkeypatch):
    from test_memory_wiki_mutations import setup
    _,access,_=setup(memory)
    assert access._personal_store.database is None
    first=access.maintain_expired(monotonic=lambda:0.,wall_clock=lambda:NOW)
    assert first['personal']['reason']=='not_open'
    second=access.maintain_expired(monotonic=lambda:1.,wall_clock=lambda:NOW)
    assert second['workspace']['reason']=='not_due' and access._personal_store.database is None
    access.settings=access.settings.model_copy(update={'cleanup_interval_seconds':0})
    assert access.maintain_expired(monotonic=lambda:1.,wall_clock=lambda:NOW)['workspace']['reason']=='no_candidates'


def test_forgotten_tombstone_and_suppression_are_retained(store):
    p=page();m=record(p['page_id'],status='forgotten',body='',expires_at=NOW-1)
    save(store,[p],[m])
    with store.database.transaction(write=True) as c:c.execute('INSERT INTO naiyue_memory_suppression VALUES(?,?)',('hash',NOW))
    result=store.memory.prune_expired(now=NOW+1000000,grace_days=0,batch_size=1)
    assert result['deleted_memories']==0
    assert store.database.connection.execute('SELECT COUNT(*) FROM naiyue_memory_suppression').fetchone()[0]==1
    assert store.database.connection.execute('SELECT status FROM naiyue_memories').fetchone()[0]=='forgotten'


def test_old_version_cleanup_does_not_delete_current_sources(store):
    p=page();m=record(p['page_id']);save(store,[p],[m])
    row=dict(m['row'],version=2)
    save(store,[dict(p,expected_version=1)],[dict(row=row,sources=m['sources'],expected_version=1)])
    result=store.memory.prune_expired(now=NOW+1,grace_days=0,batch_size=1)
    assert result['deleted_versions']==1 and result['deleted_memories']==0
    assert len(store.memory.wiki_sources([(row['memory_id'],2)]))==1


def test_personal_throttle_shared_by_two_accesses(memory):
    from test_memory_wiki_mutations import setup
    from naiyue_test_agent.application.memory.memory_access import MemoryAccess
    _,access,_=setup(memory)
    personal=access._personal_store
    personal.repository().prepare_wiki(now=NOW)
    other=MemoryAccess(access.storage,access.settings,personal.path.parent,personal=personal)
    first=access.maintain_expired(monotonic=lambda:0.,wall_clock=lambda:NOW)
    second=other.maintain_expired(monotonic=lambda:1.,wall_clock=lambda:NOW)
    assert first['personal']['reason']=='no_candidates' and second['personal']['reason']=='not_due'
    assert second['workspace']['reason']=='no_candidates'


def test_cleanup_rollback_does_not_partially_delete_sources(store):
    import sqlite3
    p=page();m=record(p['page_id'],expires_at=NOW-1);save(store,[p],[m])
    with store.database.transaction(write=True) as c:
        c.execute("CREATE TRIGGER fail_gc BEFORE DELETE ON naiyue_memory_versions BEGIN SELECT RAISE(ABORT,'injected'); END")
    from naiyue_test_agent.contracts.memory import MemoryReadError
    with pytest.raises(MemoryReadError):store.memory.prune_expired(now=NOW,grace_days=0,batch_size=2)
    assert len(store.memory.wiki_sources([(m['row']['memory_id'],1)]))==1
    assert store.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==1


def test_links_cleaned_in_bounded_batches_before_empty_page(store):
    p=page();others=[page() for _ in range(3)]
    memories=[record(o['page_id']) for o in others]
    save(store,[p,*others],memories,links=[dict(page_id=o['page_id'],targets=[dict(scope='workspace',page_id=p['page_id'],description='')]) for o in others])
    result=store.memory.prune_expired(now=NOW,grace_days=0,batch_size=1)
    assert result['deleted_links']==1 and result['deleted_pages']==0 and result['more_work']
    for _ in range(2):result=store.memory.prune_expired(now=NOW,grace_days=0,batch_size=1)
    assert result['deleted_links']==1 and result['deleted_pages']==1


def test_external_link_scan_progresses_past_valid_first_page(memory):
    from test_memory_wiki_mutations import setup
    from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
    _,access,workspace=setup(memory)
    access.settings=access.settings.model_copy(update={'cleanup_interval_seconds':0,'cleanup_batch_size':1})
    personal=access._personal_store.repository();personal.prepare_wiki(now=NOW)
    p=dict(page(),page_id='a'*32);dead='f'*32
    with personal.wiki_edit('personal',None,now=NOW,capacity=30) as writer:
        writer.apply(pages=[p],memories=[record(p['page_id'],scope='personal',workspace_id=None)])
    origin=page()
    with access.storage.memory.wiki_edit('workspace',workspace,now=NOW,capacity=30) as writer:
        writer.apply(pages=[origin],memories=[record(origin['page_id'],workspace_id=workspace)])
    with access.storage.database.transaction(write=True) as c:
        c.executemany('INSERT INTO naiyue_memory_page_links VALUES(?,?,?,?)',[(origin['page_id'],'personal',pid,'') for pid in (p['page_id'],dead)])
    for _ in range(3):access.maintain_expired(monotonic=lambda:1.,wall_clock=lambda:NOW)
    rows=access.storage.database.connection.execute('SELECT to_page_id FROM naiyue_memory_page_links').fetchall()
    assert [r[0] for r in rows]==[p['page_id']]


def test_history_read_cursor_rejects_gc_changed_projection(memory):
    import time
    from test_memory_wiki_mutations import setup,args,call
    from test_memory_lifecycle import source
    from naiyue_test_agent.contracts.memory import MemoryReadError
    _,access,workspace=setup(memory);now=time.time()
    request=args(sources=[source(access,'caller',at=now-10)])
    request['proposal']['body']='完整正文'*150
    item=call(memory,request,now=now)['item']
    item=call(memory,dict(action='update',scope='workspace',memory_id=item['memory_id'],expected_version=1,
        change={'mode':'confirm'},sources=[source(access,'caller',text='重新确认',at=now+1)]),now=now+1)['item']
    access.settings=access.settings.model_copy(update={'wiki_read_tokens':1000})
    query=dict(action='read',scope='workspace',memory_id=item['memory_id'],include_history=True)
    first=access.query_wiki('caller',workspace,query,now=now+2)
    assert first['has_more']
    access.storage.memory.prune_expired(now=now+2,grace_days=0,batch_size=1)
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):
        access.query_wiki('caller',workspace,dict(query,cursor=first['next_cursor']),now=now+2)


def test_project_failure_does_not_block_open_personal(memory,monkeypatch,caplog):
    from test_memory_wiki_mutations import setup
    from naiyue_test_agent.storage.memory import MemoryRepo
    _,access,_=setup(memory);personal=access._personal_store.repository();personal.prepare_wiki(now=NOW)
    original=MemoryRepo.prune_expired
    def prune(repo,**kwargs):
        if repo.database is access.storage.database:raise OSError('injected')
        return original(repo,**kwargs)
    monkeypatch.setattr(MemoryRepo,'prune_expired',prune)
    result=access.maintain_expired(monotonic=lambda:0.,wall_clock=lambda:NOW)
    assert result['workspace']['reason']=='failed' and result['workspace']['more_work'] is None
    assert result['personal']['reason']=='no_candidates' and 'memory_retention_failed' in caplog.text


def test_close_waits_for_maintenance_owner(memory,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from test_memory_wiki_mutations import setup
    from naiyue_test_agent.storage.memory import MemoryRepo
    _,access,_=setup(memory);entered=Event();release=Event();order=[]
    original=MemoryRepo.prune_expired
    def prune(repo,**kwargs):
        entered.set();release.wait()
        result=original(repo,**kwargs);order.append('gc');return result
    monkeypatch.setattr(MemoryRepo,'prune_expired',prune)
    def close():access.close();order.append('close')
    with ThreadPoolExecutor(max_workers=2) as pool:
        running=pool.submit(access.maintain_expired,monotonic=lambda:0.,wall_clock=lambda:NOW)
        assert entered.wait(timeout=10)
        closing=pool.submit(close)
        release.set();running.result(timeout=10);closing.result(timeout=10)
    assert order==['gc','close']


def test_personal_target_probe_failure_still_prunes_workspace(memory,monkeypatch):
    from test_memory_wiki_mutations import setup
    from naiyue_test_agent.storage.memory import MemoryRepo
    _,access,workspace=setup(memory)
    access._personal_store.repository().prepare_wiki(now=NOW)
    p=page()
    with access.storage.memory.wiki_edit('workspace',workspace,now=NOW,capacity=30) as w:
        w.apply(pages=[p],memories=[record(p['page_id'],workspace_id=workspace,expires_at=NOW-1)])
    with access.storage.database.transaction(write=True) as c:
        c.execute('INSERT INTO naiyue_memory_page_links VALUES(?,?,?,?)',(p['page_id'],'personal','f'*32,''))
    monkeypatch.setattr(MemoryRepo,'wiki_existing_pages',lambda *a:(_ for _ in ()).throw(OSError('probe failed')))
    result=access.maintain_expired(monotonic=lambda:0.,wall_clock=lambda:NOW+8*86400)
    assert result['workspace']['deleted_memories']==1


def test_more_work_includes_remaining_external_links(store):
    pages=[page() for _ in range(3)]
    save(store,pages,[record(p['page_id']) for p in pages])
    with store.database.transaction(write=True) as c:
        c.executemany('INSERT INTO naiyue_memory_page_links VALUES(?,?,?,?)',[(p['page_id'],'personal','f'*32,'') for p in pages])
    result=store.memory.prune_expired(now=NOW,grace_days=0,batch_size=2,missing_personal=['f'*32])
    assert result['deleted_links']==2 and result['more_work']


def test_fixed_gc_batch_does_not_scan_all_live_pages(tmp_path):
    costs=[]
    for count in (100,1000):
        location=tmp_path/str(count);location.mkdir()
        fixture=store.__wrapped__(location);db=next(fixture)
        try:
            pages=[page() for _ in range(count)]
            save(db,pages,[record(p['page_id']) for p in pages])
            steps=[0]
            def progress():steps[0]+=1;return 0
            db.database.connection.set_progress_handler(progress,100)
            db.memory.prune_expired(now=NOW,grace_days=0,batch_size=1)
            db.database.connection.set_progress_handler(None,0)
            costs.append(steps[0])
        finally:
            try:next(fixture)
            except StopIteration:pass
    assert costs[1]<=max(10,costs[0]*3)
