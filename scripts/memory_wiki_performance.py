"""Synthetic on-disk Wiki scaling evidence; never opens configured user stores."""
import argparse,json,statistics,tempfile,time,tracemalloc
from pathlib import Path
from uuid import uuid4
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter,SOURCES
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.memory_settings import MemorySettings


def measure(access,workspace,operation,repeats):
    timings=[];counts=[];sizes=[]
    for _ in range(repeats):
        statements=[]
        access.storage.database.connection.set_trace_callback(lambda sql:statements.append(sql.split(' ',1)[0]))
        start=time.perf_counter()
        value=operation()
        timings.append((time.perf_counter()-start)*1000);counts.append(len(statements))
        sizes.append(len(json.dumps(value,ensure_ascii=False).encode()))
        access.storage.database.connection.set_trace_callback(None)
    ordered=sorted(timings[1:])
    return dict(samples_ms=timings,first_ms=timings[0],warm_p50_ms=statistics.median(timings[1:]),warm_p95_ms=ordered[min(len(ordered)-1,int(len(ordered)*.95))],sql_calls=counts,returned_bytes=sizes)


def benchmark(count,repeats):
    with tempfile.TemporaryDirectory(prefix='nyt-wiki-perf-') as tmp:
        root=Path(tmp).resolve();workspace=str(uuid4());now=time.time()
        with ProjectStore(root/'state.sqlite') as store:
            store.sessions.bind_workspace(workspace,root);store.memory.prepare_wiki(now=now)
            store.sessions.create('probe',dict(thread_id='probe',model_id='benchmark',workspace_root=root,active_tool_names=['memory'],permission_mode='Default'))
            pages=[];records=[]
            for i in range(count):
                if i%30==0:pages.append(WikiWriter.new_page('stable_knowledge',f'主题{i//30:05}','合成性能材料'))
                mid=uuid4().hex
                source=dict.fromkeys(SOURCES);source.update(source_workspace_id=workspace,session_id='probe',thread_id='probe',message_id=f's{i}',revision=1,
                    conversation_revision=0,role='human',occurred_at=now,quote='合成来源',start_offset=0,end_offset=4,
                    evidence_format=1,user_origin=True,tool_success=False,verified_at=now,supports_scope=False)
                row=dict(memory_id=mid,version=1,scope='workspace',workspace_id=workspace,page_id=pages[-1]['page_id'],status='active',title=f'长期条目{i}',
                    body='合成的稳定约定，用于测量固定页读取。'*20,aliases_json='[]',tags_json='[]',machine=None,module=None,target=None,subject=f'事项{i}',basis='user_declaration',
                    created_at=now,updated_at=now,evidence_at=now,last_confirmed_at=now,ttl_days=30,expires_at=now+30*86400,gaps_json='[]',reason='',deadline_at=None)
                records.append(dict(row=row,sources=[source],expected_version=None))
            with store.memory.wiki_edit('workspace',workspace,now=now,capacity=30) as writer:writer.apply(pages=pages,memories=records)
            access=MemoryAccess(store,MemorySettings(),root/'data')
            try:
                tracemalloc.start()
                operations=dict(directory=lambda:access.query_wiki('probe',workspace,dict(action='list_pages',scope='workspace',topic='stable_knowledge')),
                    page=lambda:access.query_wiki('probe',workspace,dict(action='read_page',scope='workspace',page_id=pages[0]['page_id'])),
                    source=lambda:access.query_wiki('probe',workspace,dict(action='read',scope='workspace',memory_id=records[0]['row']['memory_id'])),
                    gc=lambda:store.memory.prune_expired(now=now,grace_days=7,batch_size=100))
                measurements={k:measure(access,workspace,v,repeats) for k,v in operations.items()}
                _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
                plans=[list(r) for r in store.database.connection.execute('EXPLAIN QUERY PLAN SELECT * FROM naiyue_memories WHERE page_id=? AND status=? AND expires_at>? ORDER BY expires_at,memory_id LIMIT 31',(pages[0]['page_id'],'active',now))]
                return dict(memories=count,pages=len(pages),measurements=measurements,python_peak_bytes=peak,query_plan=plans,
                    boundaries='Synthetic baseline. First sample is first operation only, OS cache not flushed. tracemalloc excludes SQLite native memory. No concurrent lock-wait measurement.')
            finally:access.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    report=dict(synthetic=True,volumes=[benchmark(n,11) for n in (2000,20000)])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(volumes=[r['memories'] for r in report['volumes']],output=str(args.output))))


if __name__=='__main__':main()
