"""User-visible Wiki navigation stays bounded, complete and read-only."""
import json
import pytest
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.tools.memory_wiki_actions import compile_wiki_request
from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
from test_memory_readonly import memory
from memory_wiki_helpers import record, NOW


def decode_content(result):
    content=result['content']
    assert content.startswith('```json\n') and content.endswith('\n```')
    return json.loads(content[8:-4])


def seed_wiki(memory, count=1, *, body='规则', candidates=False):
    storage,access,workspace,_=memory
    storage.memory.prepare_wiki(now=NOW)
    pages=[]
    with storage.memory.wiki_edit('workspace',workspace,now=NOW,capacity=200) as writer:
        for i in range(count):
            p=WikiWriter.new_page('working_agreements',f'后台规则{i:03}', '说明'*30)
            writer.apply(pages=[p],memories=[record(p['page_id'],workspace_id=workspace,body=body,
                status='candidate' if candidates else 'active')])
            pages.append(p)
    return storage,access,workspace,pages


def query(memory, **args):
    _,access,workspace,_=memory
    return access.query_wiki('caller',workspace,compile_wiki_request({'request':args}),now=NOW)


def test_directory_reads_all_pages_without_loading_bodies_or_renewing(memory):
    storage,access,workspace,pages=seed_wiki(memory,65)
    before=storage.database.connection.total_changes
    cursor=None;seen=[]
    for _ in range(100):
        result=query(memory,action='list_pages',scope='workspace',topic='working_agreements',cursor=cursor)
        assert result['budget']['estimated_tokens']<=2000
        assert result['budget']['bytes']==len(json.dumps(result,ensure_ascii=False).encode())
        seen.extend(i['page_id'] for i in result['items'])
        assert '规则正文' not in result['content']
        if not result['has_more']:break
        assert result['next_cursor'] and result['next_cursor']!=cursor
        cursor=result['next_cursor']
    else:pytest.fail('pagination did not finish')
    assert seen==[p['page_id'] for p in pages]
    assert storage.database.connection.total_changes==before


def test_long_unicode_entry_can_be_read_completely_with_conditions(memory):
    body='先讲职责，再举例。😀\n'*90
    storage,_,workspace,pages=seed_wiki(memory,body=body)
    cursor=None;pieces=[]
    for _ in range(100):
        result=query(memory,action='read_page',scope='workspace',page_id=pages[0]['page_id'],cursor=cursor)
        assert result['budget']['estimated_tokens']<=2000
        payload=decode_content(result)
        pieces.extend(e['body'] for e in payload['entries'])
        assert all('conditions' in e and 'expires_at' in e for e in payload['entries'])
        if not result['has_more']:break
        assert result['next_cursor']!=cursor
        cursor=result['next_cursor']
    else:pytest.fail('entry pagination did not finish')
    assert ''.join(pieces)==body


def test_cursor_changed_scope_page_version_expiry_and_tampering_are_rejected(memory):
    storage,access,workspace,pages=seed_wiki(memory,body='规则😀'*250)
    args=dict(action='read_page',scope='workspace',page_id=pages[0]['page_id'])
    access.settings=access.settings.model_copy(update={'wiki_read_tokens':700})
    result=query(memory,**args);cursor=result['next_cursor'];assert cursor
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):
        query(memory,**dict(args,scope='personal'),cursor=cursor)
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):
        query(memory,**args,cursor=cursor[:-2]+'xx')
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):
        access.query_wiki('caller',workspace,compile_wiki_request({'request':dict(args,cursor=cursor)}),now=NOW+31*86400)
    with storage.memory.wiki_edit('workspace',workspace,now=NOW,capacity=30) as writer:
        writer.apply(pages=[dict(pages[0],expected_version=1,title='改名')])
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):query(memory,**args,cursor=cursor)


def test_candidate_hidden_and_tiny_budget_fails_without_nonprogress_cursor(memory):
    storage,access,workspace,pages=seed_wiki(memory,candidates=True)
    assert query(memory,action='list_pages',scope='workspace',topic='working_agreements')['items']==[]
    assert decode_content(query(memory,action='read_page',scope='workspace',page_id=pages[0]['page_id']))['entries']==[]
    access.settings=access.settings.model_copy(update={'wiki_read_tokens':1})
    with pytest.raises(MemoryReadError,match='memory_budget_too_small'):
        query(memory,action='list_topics',scope='workspace')


def test_content_is_markdown_and_original_source_readability_is_not_invented(memory):
    _,_,_,pages=seed_wiki(memory)
    result=query(memory,action='read_page',scope='workspace',page_id=pages[0]['page_id'])
    payload=decode_content(result)
    assert payload['entries'][0]['sources'][0]['original_state']=='other_workspace'
    assert 'quote' not in payload['entries'][0]['sources'][0]


def test_cursor_is_bound_to_generation_and_caller(memory):
    storage,access,workspace,pages=seed_wiki(memory,body='规则😀'*250)
    access.settings=access.settings.model_copy(update={'wiki_read_tokens':900})
    args=dict(action='read_page',scope='workspace',page_id=pages[0]['page_id'])
    cursor=query(memory,**args)['next_cursor'];assert cursor
    storage.sessions.create('other',dict(thread_id='other',model_id='A',workspace_root=memory[3],
        active_tool_names=['memory'],permission_mode='Default'))
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):
        access.query_wiki('other',workspace,compile_wiki_request({'request':dict(args,cursor=cursor)}),now=NOW)
    with storage.database.transaction(write=True) as c:
        c.execute('UPDATE naiyue_memory_info SET generation_id=?',('b'*32,))
    with pytest.raises(MemoryReadError,match='memory_cursor_stale'):query(memory,**args,cursor=cursor)


def test_directory_sql_projection_excludes_bodies_and_sources(memory):
    storage,_,_,_=seed_wiki(memory,5)
    seen=[];c=storage.database.connection;c.set_trace_callback(seen.append)
    try:query(memory,action='list_pages',scope='workspace',topic='working_agreements')
    finally:c.set_trace_callback(None)
    statements=[s.lower() for s in seen if s.lstrip().lower().startswith('select')]
    assert not any('body' in s or 'naiyue_memory_sources' in s for s in statements)


def test_frontend_rejects_hidden_projection_but_controlled_reader_can_see_candidate(memory):
    from pydantic import ValidationError
    from naiyue_test_agent.application.memory.memory_wiki_reads import query as controlled_query
    storage,access,workspace,pages=seed_wiki(memory,candidates=True)
    args=dict(action='list_pages',scope='workspace',topic='working_agreements')
    with pytest.raises(ValidationError):query(memory,**dict(args,include_candidates=True))
    request=compile_wiki_request({'request':args})
    with access._lock:
        result=controlled_query(access,workspace,'caller',request,NOW,include_candidates=True)
    assert [p['page_id'] for p in result['items']]==[pages[0]['page_id']]
