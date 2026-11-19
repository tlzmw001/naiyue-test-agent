import json
import statistics
import time
import tracemalloc
import pytest
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter, FIELDS
from memory_wiki_helpers import NOW, record, evidence


def test_keyset_catalog_and_candidate_projection(tmp_path):
    with ProjectStore(tmp_path/'queries.sqlite') as store:
        store.sessions.bind_workspace('00000000-0000-4000-8000-000000000001',tmp_path)
        store.memory.prepare_wiki(now=NOW)
        owner='00000000-0000-4000-8000-000000000001'
        pages=[WikiWriter.new_page('stable_knowledge',f'页{i:03}','用途') for i in range(12)]
        with store.memory.wiki_edit('workspace',owner,now=NOW,capacity=30) as w:
            w.apply(pages=pages,memories=[record(p['page_id']) for p in pages])
        seen=[];after=None
        while True:
            rows=store.memory.wiki_pages('workspace',owner,'stable_knowledge',now=NOW,after=after,limit=3)
            if not rows:break
            seen.extend(r['page_id'] for r in rows)
            after=(rows[-1]['title'],rows[-1]['page_id'])
        assert seen==[p['page_id'] for p in pages]
        assert len(set(seen))==12


@pytest.mark.parametrize('size',[2000,20000])
def test_disk_scale_queries_use_page_indexes_and_batch_sources(tmp_path,size):
    owner='00000000-0000-4000-8000-000000000001'
    with ProjectStore(tmp_path/'scale.sqlite') as store:
        store.sessions.bind_workspace(owner,tmp_path);store.memory.prepare_wiki(now=NOW)
        pages=[WikiWriter.new_page('stable_knowledge',f'主题{i:04}','主题用途') for i in range((size+29)//30)]
        with store.memory.wiki_edit('workspace',owner,now=NOW,capacity=30) as writer:writer.apply(pages=pages)
        with store.database.transaction(write=True) as c:
            # Synthetic perf data bypasses application semantics, not the scoped read path under test.
            rows=[]
            for i in range(size):
                r=record(pages[i//30]['page_id'])['row'];r['memory_id']=f'{i:032x}';rows.append(tuple(r[k] for k in FIELDS))
            c.executemany('INSERT INTO naiyue_memories('+','.join(FIELDS)+') VALUES('+','.join('?' for _ in FIELDS)+')',rows)
            plans=[r[3] for r in c.execute('EXPLAIN QUERY PLAN SELECT memory_id FROM naiyue_memories WHERE page_id=? AND status=? AND expires_at>? ORDER BY expires_at,memory_id LIMIT 30',
                (pages[0]['page_id'],'active',NOW))]
            assert any('wiki_members' in p and 'SEARCH' in p for p in plans)
        with store.database.transaction(write=True) as c:
            from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import SOURCES
            tuples=[]
            for i in range(30):
                for n in (1,2):
                    e=evidence(number=n);tuples.append((f'{i:032x}',1,*(e.get(k) for k in SOURCES)))
            c.executemany('INSERT INTO naiyue_memory_sources(memory_id,memory_version,'+','.join(SOURCES)+') VALUES('+','.join('?' for _ in range(len(SOURCES)+2))+')',tuples)
            expired=[]
            for i in range(size//4):
                r=record(pages[0]['page_id'],expires_at=NOW-1)['row'];r['memory_id']=f'{size+i:032x}'
                expired.append(tuple(r[k] for k in FIELDS))
            c.executemany('INSERT INTO naiyue_memories('+','.join(FIELDS)+') VALUES('+','.join('?' for _ in FIELDS)+')',expired)
            c.executemany('INSERT INTO naiyue_memory_versions VALUES(?,?,?,?)',[(f'{0:032x}',n,'{}',NOW-10) for n in range(1,101)])
        traced=[];store.database.connection.set_trace_callback(traced.append)
        before=store.database.connection.total_changes
        members=store.memory.wiki_members('workspace',owner,pages[0]['page_id'],now=NOW)
        sources=store.memory.wiki_sources([(r['memory_id'],r['version']) for r in members])
        store.database.connection.set_trace_callback(None)
        assert len(members)==30 and len(sources)==60
        assert store.database.connection.total_changes==before
        assert len([s for s in traced if 'SELECT * FROM naiyue_memory_sources' in s])==1
        assert all('OFFSET' not in s.upper() for s in traced)

        elapsed=[];tracemalloc.start()
        try:
            for _ in range(20):
                start=time.perf_counter()
                store.memory.wiki_members('workspace',owner,pages[0]['page_id'],now=NOW)
                elapsed.append((time.perf_counter()-start)*1000)
            _,peak=tracemalloc.get_traced_memory()
        finally:tracemalloc.stop()
        print(json.dumps(dict(scale=size,expired=size//4,history_versions=100,returned_rows=len(members),
            returned_source_rows=len(sources),member_query_plan=plans,
            source_selects=sum('SELECT * FROM naiyue_memory_sources' in q for q in traced),
            p50_ms=statistics.median(elapsed),p95_ms=sorted(elapsed)[18],python_peak_bytes=peak,
            material_bytes=len(json.dumps(members,ensure_ascii=False).encode()),
            boundary='temporary disk SQLite; warm repeated member reads; Python allocations exclude SQLite native memory')))
