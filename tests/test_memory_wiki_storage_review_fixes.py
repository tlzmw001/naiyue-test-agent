"""Regression coverage for Wiki source round trips and failed savepoint rollback."""
import sqlite3
import pytest
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.storage.memory_sql import memory_wiki_schema
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from memory_wiki_helpers import NOW, record
from test_memory_wiki_storage import store, page, save


def test_personal_sources_roundtrip_preserves_boolean_types_and_can_update():
    from naiyue_test_agent.storage.memory_sql import memory_wiki_queries, lexical
    with sqlite3.connect(':memory:') as c:
        c.row_factory=sqlite3.Row
        lexical.configure(c)
        memory_wiki_schema.create(c,scope='personal',workspace_id=None,now=NOW)
        c.commit();c.execute('BEGIN')
        writer=WikiWriter(c,'personal',None,now=NOW,capacity=30)
        p=page();m=record(p['page_id'],scope='personal',workspace_id=None)
        writer.apply(pages=[p],memories=[m])
        sources=memory_wiki_queries.sources(c,[(m['row']['memory_id'],1)])
        assert sources[0]['user_origin'] is True
        assert sources[0]['tool_success'] is False
        assert sources[0]['supports_scope'] is True
        writer.apply(pages=[dict(p,expected_version=1)],memories=[
            dict(m,row=dict(m['row'],version=2),expected_version=1,sources=sources)])
        assert writer.current(m['row']['memory_id'],'personal',None)['version']==2


def test_failed_savepoint_rollback_cannot_commit_when_original_error_caught(store):
    p=page();save(store,[p],[record(p['page_id'])],capacity=1)
    c=store.database.connection
    def deny_savepoint_rollback(action,a,b,database,trigger):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_SAVEPOINT and a=='ROLLBACK' else sqlite3.SQLITE_OK
    c.set_authorizer(deny_savepoint_rollback)
    try:
        with pytest.raises(MemoryReadError,match='memory_wiki_group_rollback_failed'):
            with store.memory.wiki_edit('workspace','00000000-0000-4000-8000-000000000001',now=NOW,capacity=1) as writer:
                with pytest.raises(MemoryReadError,match='memory_topic_full'):
                    writer.apply(pages=[dict(p,expected_version=1,title='must not persist')],memories=[record(p['page_id'])])
    finally:
        c.set_authorizer(None)
    row=dict(c.execute('SELECT title,version FROM naiyue_memory_pages').fetchone())
    assert row==dict(title=p['title'],version=1)
    assert c.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==1
    assert c.execute('SELECT catalog_revision FROM naiyue_memory_info').fetchone()[0]==1
    assert not c.in_transaction
