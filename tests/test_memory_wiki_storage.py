from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from memory_wiki_helpers import NOW, record, evidence


@pytest.fixture
def store(tmp_path):
    with ProjectStore(tmp_path/'project.sqlite') as store:
        store.sessions.bind_workspace('00000000-0000-4000-8000-000000000001',tmp_path)
        store.memory.prepare_wiki(now=NOW)
        yield store


def save(store, pages=(), memories=(), *, capacity=30, links=()):
    with store.memory.wiki_edit('workspace','00000000-0000-4000-8000-000000000001',now=NOW,capacity=capacity) as writer:
        return writer.apply(pages=pages,memories=memories,links=links)


def page():
    return WikiWriter.new_page('working_agreements','后台规则','后台任务的行为约定')


def test_create_read_and_current_version_sources(store):
    p=page();m=record(p['page_id']);save(store,[p],[m])
    rows=store.memory.wiki_pages('workspace','00000000-0000-4000-8000-000000000001','working_agreements',now=NOW)
    assert [r['page_id'] for r in rows]==[p['page_id']]
    assert 'body' not in rows[0]
    members=store.memory.wiki_members('workspace','00000000-0000-4000-8000-000000000001',p['page_id'],now=NOW)
    assert members[0]['body']=='先验证' and members[0]['version']==1
    assert len(store.memory.wiki_sources([(m['row']['memory_id'],1)]))==1


def test_capacity_and_whole_group_rollback_even_when_caught(store):
    p=page();save(store,[p],[record(p['page_id']) for _ in range(30)])
    with store.memory.wiki_edit('workspace','00000000-0000-4000-8000-000000000001',now=NOW,capacity=30) as writer:
        with pytest.raises(MemoryReadError,match='memory_topic_full'):
            writer.apply(pages=[dict(p,expected_version=1,title='不应保存')],memories=[record(p['page_id'])])
    assert store.memory.wiki_pages('workspace','00000000-0000-4000-8000-000000000001','working_agreements',now=NOW)[0]['title']=='后台规则'
    assert len(store.memory.wiki_members('workspace','00000000-0000-4000-8000-000000000001',p['page_id'],now=NOW))==30


def test_candidate_counts_capacity_and_expired_records_do_not(store):
    p=page();save(store,[p],[record(p['page_id'],status='candidate')],capacity=1)
    assert store.memory.wiki_pages('workspace','00000000-0000-4000-8000-000000000001','working_agreements',now=NOW)==[]
    assert len(store.memory.wiki_pages('workspace','00000000-0000-4000-8000-000000000001','working_agreements',now=NOW,include_candidates=True))==1
    with pytest.raises(MemoryReadError,match='memory_topic_full'):save(store,[dict(p,expected_version=1)],[record(p['page_id'])],capacity=1)
    assert store.memory.wiki_members('workspace','00000000-0000-4000-8000-000000000001',p['page_id'],now=NOW+31*86400,include_candidates=True)==[]


def test_move_preserves_content_and_retires_version_with_cas(store):
    a,b=page(),page();m=record(a['page_id']);save(store,[a,b],[m])
    changed=deepcopy(m);changed.update(expected_version=1);changed['row'].update(version=2,page_id=b['page_id'])
    save(store,[dict(a,expected_version=1),dict(b,expected_version=1)],[changed])
    assert store.memory.wiki_members('workspace','00000000-0000-4000-8000-000000000001',a['page_id'],now=NOW)==[]
    assert store.memory.wiki_members('workspace','00000000-0000-4000-8000-000000000001',b['page_id'],now=NOW)[0]['expires_at']==m['row']['expires_at']
    with pytest.raises(MemoryReadError,match='memory_conflict'):save(store,[dict(b,expected_version=1)],[changed])
    with store.database.transaction() as c:
        assert c.execute('SELECT superseded_at FROM naiyue_memory_versions WHERE version=1').fetchone()[0]==NOW


def test_cross_scope_and_source_overflow_rollback(store):
    p=page();bad=record(p['page_id'],scope='personal',workspace_id=None)
    with pytest.raises(MemoryReadError,match='scope'):save(store,[p],[bad])
    assert store.memory.wiki_pages('workspace','00000000-0000-4000-8000-000000000001','working_agreements',now=NOW)==[]
    bad=record(p['page_id']);bad['sources']=[evidence(number=n) for n in range(9)]
    with pytest.raises(MemoryReadError,match='source'):save(store,[p],[bad])


def test_two_writers_only_one_expected_page_commit(store):
    p=page();save(store,[p],[record(p['page_id'])]);barrier=Barrier(2)
    def write():
        barrier.wait()
        try:save(store,[dict(p,expected_version=1)],[record(p['page_id'])]);return 'ok'
        except MemoryReadError as error:return str(error)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:write(),range(2)))
    assert sorted(results)==['memory_conflict','ok']


def test_read_wrong_owner_and_unlisted_page_write_rejected(store):
    p=page();save(store,[p],[record(p['page_id'])])
    with pytest.raises(MemoryReadError,match='scope'):store.memory.wiki_pages('workspace','other','working_agreements',now=NOW)
    with pytest.raises(MemoryReadError,match='memory_page_version_required'):save(store,memories=[record(p['page_id'])])


def test_member_cas_failure_rolls_back_page_metadata(store):
    p=page();m=record(p['page_id']);save(store,[p],[m])
    wrong=dict(m,row=dict(m['row'],version=3),expected_version=2)
    with pytest.raises(MemoryReadError,match='memory_conflict'):
        save(store,[dict(p,expected_version=1,title='不应改变')],[wrong])
    assert store.memory.wiki_pages('workspace','00000000-0000-4000-8000-000000000001','working_agreements',now=NOW)[0]['version']==1


def test_split_is_atomic_and_sources_survive_original_removal(store):
    a,b=page(),page();m=record(a['page_id']);save(store,[a],[m])
    changed=dict(m,row=dict(m['row'],version=2,page_id=b['page_id']),expected_version=1)
    save(store,[dict(a,expected_version=1),b],[changed])
    mid=m['row']['memory_id']
    with store.database.transaction(write=True) as c:
        c.execute('DELETE FROM naiyue_memory_sources WHERE memory_id=? AND memory_version=1',(mid,))
        c.execute('DELETE FROM naiyue_memory_versions WHERE memory_id=? AND version=1',(mid,))
    assert store.memory.wiki_sources([(mid,2)])[0]['quote']=='保留来源'
    assert store.memory.wiki_members('workspace','00000000-0000-4000-8000-000000000001',b['page_id'],now=NOW)[0]['body']=='先验证'


def test_link_rules_and_catalog_revision(store):
    a,b=page(),page();save(store,[a,b],[record(a['page_id']),record(b['page_id'])],
        links=[dict(page_id=a['page_id'],targets=[dict(scope='workspace',page_id=b['page_id'],description='相关')])])
    assert store.memory.wiki_links('workspace','00000000-0000-4000-8000-000000000001',a['page_id'],now=NOW)[0]['to_page_id']==b['page_id']
    with store.database.transaction() as c:
        assert c.execute('SELECT catalog_revision FROM naiyue_memory_info').fetchone()[0]==1
    with pytest.raises(MemoryReadError,match='memory_link_target'):
        save(store,[dict(a,expected_version=1)],links=[dict(page_id=a['page_id'],targets=[dict(scope='workspace',page_id='outside-project',description='坏')])])
    assert len(store.memory.wiki_links('workspace','00000000-0000-4000-8000-000000000001',a['page_id'],now=NOW))==1


def test_lowered_capacity_allows_nonincreasing_update(store):
    p=page();m=record(p['page_id']);save(store,[p],[m,record(p['page_id'])])
    changed=dict(m,row=dict(m['row'],version=2),expected_version=1)
    save(store,[dict(p,expected_version=1)],[changed],capacity=1)
    with pytest.raises(MemoryReadError,match='memory_topic_full'):
        save(store,[dict(p,expected_version=2)],[record(p['page_id'])],capacity=1)


def test_expiry_during_plan_and_deleted_target_do_not_create_replacement(store):
    p=page();m=record(p['page_id']);save(store,[p],[m])
    changed=dict(m,row=dict(m['row'],version=2),expected_version=1)
    with pytest.raises(MemoryReadError,match='memory_conflict'):
        with store.memory.wiki_edit('workspace',m['row']['workspace_id'],now=m['row']['expires_at'],capacity=30) as writer:
            writer.apply(pages=[dict(p,expected_version=1)],memories=[changed])
    with store.database.transaction(write=True) as c:
        c.execute('DELETE FROM naiyue_memories WHERE memory_id=?',(m['row']['memory_id'],))
    with pytest.raises(MemoryReadError,match='memory_conflict'):
        save(store,[dict(p,expected_version=1)],[changed])
