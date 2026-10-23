"""Product memory queries use bounded, versioned authority without a model."""

import json
from uuid import uuid4

import pytest

from naiyue_test_agent.application.session_storage import SessionStorage
from naiyue_test_agent.application.memory_access import MemoryAccess, MemoryReadError
from naiyue_test_agent.application.memory_personal import PersonalMemory
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.tools.memory import MemoryInput


def seed(connection, number, *, scope='workspace', workspace_id=None, title='测试环境', body='使用项目venv', **changes):
    row = dict(memory_id=f'{number:032x}', version=1, scope=scope, workspace_id=workspace_id,
        kind='context', status='active', title=title, body=body, aliases_json='[]', tags_json='[]',
        machine=None, module=None, target=None, subject=None, basis='user_declaration',
        created_at=1000000000.0, updated_at=1000000000.0, evidence_at=1000000000.0,
        last_confirmed_at=None, ttl_days=30, expires_at=None)
    row.update(changes)
    with connection:
        connection.execute('''INSERT INTO naiyue_memories(memory_id,version,scope,workspace_id,kind,status,
            title,body,aliases_json,tags_json,machine,module,target,subject,basis,created_at,updated_at,
            evidence_at,last_confirmed_at,ttl_days,expires_at) VALUES(:memory_id,:version,:scope,:workspace_id,
            :kind,:status,:title,:body,:aliases_json,:tags_json,:machine,:module,:target,:subject,:basis,
            :created_at,:updated_at,:evidence_at,:last_confirmed_at,:ttl_days,:expires_at)''', row)
    return row['memory_id']


@pytest.fixture
def memory(tmp_path):
    identity = str(uuid4())
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        storage.bind_workspace(identity, tmp_path)
        storage.create('caller', dict(thread_id='caller', model_id='A', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        access = MemoryAccess(storage, MemorySettings(), tmp_path / 'data')
        try:yield storage, access, identity, tmp_path
        finally:access.close()


def query(memory, **arguments):
    _, access, _, root = memory
    return access.query('caller', root, MemoryInput(**arguments).model_dump(), now=2000000000.0)


def test_empty_query_is_readonly_and_personal_is_lazy(memory):
    storage, access, _, root = memory
    assert not (root / 'data/personal/memory.sqlite').exists()
    before = storage.connection.total_changes
    assert query(memory, action='list', scope='workspace')['items'] == []
    assert storage.connection.total_changes == before
    assert not (root / 'data/personal/memory.sqlite').exists()
    assert query(memory, action='list')['items'] == []
    assert (root / 'data/personal/memory.sqlite').exists()


def test_scope_status_expiry_conditions_and_reads_never_renew(memory):
    storage, _, identity, _ = memory
    c = storage.connection
    valid = seed(c, 1, workspace_id=identity, machine='windows')
    for number, changes in enumerate([{'status':'candidate'}, {'status':'inactive'}, {'status':'forgotten'},
            {'expires_at':2000000000.0}, {'workspace_id':str(uuid4())}], 2):
        seed(c, number, **{'workspace_id':identity, **changes})
    result = query(memory, action='list', scope='workspace')
    assert [r['memory_id'] for r in result['items']] == [valid]
    assert result['items'][0]['needs_verification']
    assert query(memory, action='list', scope='workspace', machine='mac')['items'] == []
    assert len(query(memory, action='list', scope='workspace', status='candidate')['items']) == 1
    assert len(query(memory, action='list', scope='workspace', status='expired')['items']) == 1
    assert len(query(memory, action='list', scope='workspace', status='all')['items']) == 4
    before = c.total_changes
    query(memory, action='read', scope='workspace', memory_id=valid, version=1)
    assert c.total_changes == before


def test_cascade_and_technical_anchors_reject_weak_or_different_environment(memory):
    storage, _, identity, _ = memory
    c = storage.connection
    seed(c, 1, workspace_id=identity, title='测试环境', body='测试执行前确认解释器')
    exact = seed(c, 2, workspace_id=identity, title='pytest测试类组织', body='使用TestOrder')
    assert [r['memory_id'] for r in query(memory, action='search', scope='workspace', query='测试类')['items']] == [exact]
    expected = seed(c, 3, workspace_id=identity, title='用例生成', body='TC-01使用.venv')
    seed(c, 4, workspace_id=identity, title='用例生成', body='TC-02使用.venv')
    result = query(memory, action='search', scope='workspace', query='用例生成 TC-01')
    assert [r['memory_id'] for r in result['items']] == [expected]
    assert query(memory, action='search', scope='workspace', query='不存在的主题')['items'] == []


def test_configurable_k_deduplicates_after_top_k_without_refill(memory):
    storage, access, identity, _ = memory
    access.settings = MemorySettings(default_limit=2, max_limit=3)
    for number in range(1, 5):seed(storage.connection, number, workspace_id=identity)
    result = query(memory, action='search', scope='workspace', query='测试环境')
    assert len(result['items']) == 1 and len(result['items'][0]['duplicate_ids']) == 1
    assert result['requested_limit'] == 2 and result['truncated']
    assert len(query(memory, action='search', scope='workspace', query='测试环境', limit=3)['items'][0]['duplicate_ids']) == 2
    with pytest.raises(MemoryReadError, match='memory_invalid_limit'):
        query(memory, action='list', scope='workspace', limit=4)


def test_current_version_and_digest_control_stale_index(memory):
    storage, _, identity, _ = memory
    item = seed(storage.connection, 1, workspace_id=identity, body='old-needle')
    with storage.connection:
        storage.connection.execute('UPDATE naiyue_memories SET body=?,version=2 WHERE memory_id=?', ('new-needle', item))
    assert query(memory, action='search', scope='workspace', query='old-needle')['items'] == []
    assert query(memory, action='search', scope='workspace', query='new-needle')['items'][0]['version'] == 2
    with pytest.raises(MemoryReadError, match='memory_version_changed'):
        query(memory, action='read', scope='workspace', memory_id=item, version=1)
    with storage.connection:
        storage.connection.execute("UPDATE naiyue_memory_search SET memory_version=1")
    assert query(memory, action='search', scope='workspace', query='new-needle')['items'] == []


def test_detail_nul_unicode_budget_and_cursor_scope(memory):
    storage, access, identity, _ = memory
    text = '前文\0正文🧪' * 100
    item = seed(storage.connection, 1, workspace_id=identity, body=text)
    seed(storage.connection, 2, workspace_id=identity)
    access.settings = MemorySettings(read_chunk_chars=57, max_output_bytes=4096)
    chunks, offset = [], 0
    for _ in range(30):
        result = query(memory, action='read', scope='workspace', memory_id=item, version=1, offset=offset)
        row, = result['items']
        assert len(json.dumps(result, ensure_ascii=False).encode()) <= 4096
        chunks.append(row['body']);offset=row['next_offset']
        if row['complete']:break
    assert ''.join(chunks) == text and offset == len(text)
    first = query(memory, action='list', scope='workspace', limit=1)
    assert first['has_more'] and first['next_cursor']
    second = query(memory, action='list', scope='workspace', limit=1, cursor=first['next_cursor'])
    assert first['items'][0]['memory_id'] != second['items'][0]['memory_id']
    with pytest.raises(MemoryReadError, match='memory_cursor_stale'):
        query(memory, action='list', scope='both', limit=1, cursor=first['next_cursor'])


def test_personal_shared_connection_and_foreign_sources_do_not_read_other_project(memory):
    storage, _, _, root = memory
    path = root / 'data/personal/memory.sqlite'
    with PersonalMemory(path, MemorySettings()) as personal:
        item = seed(personal.connection, 1, scope='personal')
    result = query(memory, action='search', query='测试环境')
    assert result['items'][0]['scope'] == 'personal' and result['items'][0]['memory_id'] == item
    assert storage.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0] == 0


def add_source(connection, item, workspace, *, quote, message='h', role='human', offset=0):
    with connection:
        connection.execute('''INSERT INTO naiyue_memory_sources(memory_id,memory_version,source_workspace_id,
            session_id,thread_id,message_id,revision,conversation_revision,role,occurred_at,quote,start_offset,end_offset)
            VALUES(?,1,?,'caller','caller',?,1,0,?,1000000000,?,?,?)''',
            (item, workspace, message, role, quote, offset, offset + len(quote)))


def test_sources_only_quote_current_visible_archive_and_foreign_reference_is_metadata(memory):
    from langchain_core.messages import HumanMessage, AIMessage
    from test_history_tool_storage import save
    storage, access, identity, _ = memory
    save(storage, 'caller', [HumanMessage(id='h',content='明确使用.venv'),
        AIMessage(id='a',content=[{'type':'reasoning','reasoning':'HIDDEN-SOURCE'},
                                {'type':'text','text':'可见结果'}])])
    item = seed(storage.connection, 1, workspace_id=identity)
    add_source(storage.connection,item,identity,quote='使用.venv',offset=2)
    add_source(storage.connection,item,identity,quote='HIDDEN-SOURCE',message='a',role='ai')
    result = query(memory,action='read',scope='workspace',memory_id=item,version=1)
    sources = result['items'][0]['sources']
    assert sources[0]['available'] and sources[0]['quote']=='使用.venv'
    assert not sources[1]['available'] and 'quote' not in sources[1]
    assert 'HIDDEN-SOURCE' not in json.dumps(result,ensure_ascii=False)
    query(memory,action='list',scope='personal')
    personal = access._personal.connection
    other = seed(personal,2,scope='personal')
    add_source(personal,other,str(uuid4()),quote='FOREIGN-SECRET')
    source, = query(memory,action='read',scope='personal',memory_id=other)['items'][0]['sources']
    assert not source['available'] and source['reason']=='other_workspace' and 'quote' not in source
    save(storage,'caller',[HumanMessage(id='h',content='来源已改写')])
    assert not query(memory,action='read',scope='workspace',memory_id=item)['items'][0]['sources'][0]['available']


def test_expansion_anchors_cannot_override_original_and_fallback_is_configurable(memory):
    storage, access, identity, _ = memory
    expected = seed(storage.connection,1,workspace_id=identity,title='代理',body='10.0.0.1走代理')
    seed(storage.connection,2,workspace_id=identity,title='代理',body='10.0.0.2走代理')
    result=query(memory,action='search',scope='workspace',query='代理 10.0.0.1',expansions=['代理 10.0.0.2'])
    assert [r['memory_id'] for r in result['items']]==[expected]
    reversed_item=seed(storage.connection,3,workspace_id=identity,title='生成用例',body='流程')
    assert query(memory,action='search',scope='workspace',query='用例生成')['items'][0]['memory_id']==reversed_item
    access.settings=MemorySettings(fallback_coverage=1.0)
    assert query(memory,action='search',scope='workspace',query='用例生成')['items']==[]


def test_exact_folding_preserves_conditions_basis_and_code_whitespace(memory):
    storage,_,identity,_=memory
    for number,changes in enumerate([{}, {'subject':'other'}, {'basis':'inference'},
            {'body':'pytest\n    pass'}, {'body':'pytest\n pass'}],1):
        seed(storage.connection,number,workspace_id=identity,title='pytest',body=changes.pop('body','pytest'),**changes)
    result=query(memory,action='search',scope='workspace',query='pytest')
    assert len(result['items'])==5 and all(not row['duplicate_ids'] for row in result['items'])


def test_query_complexity_is_bounded_and_sql_text_is_literal(memory):
    _,access,_,_=memory
    access.settings=MemorySettings(max_query_terms=2)
    with pytest.raises(MemoryReadError,match='memory_query_too_complex'):
        query(memory,action='search',scope='workspace',query='alpha beta gamma')
    access.settings=MemorySettings()
    assert query(memory,action='search',scope='workspace',query="' OR 1=1 --")['items']==[]


@pytest.mark.parametrize('column,value', [('tool_call_id','wrong-call'),('reply_id','wrong-reply')])
def test_source_call_and_reply_ids_must_match_visible_authority(memory,column,value):
    from langchain_core.messages import ToolMessage
    from test_history_tool_storage import save
    storage,_,identity,_=memory
    save(storage,'caller',[ToolMessage(id='t',tool_call_id='actual-call',content='观察成功')])
    item=seed(storage.connection,1,workspace_id=identity)
    add_source(storage.connection,item,identity,quote='观察成功',message='t',role='tool')
    with storage.connection:
        storage.connection.execute("UPDATE naiyue_memory_sources SET tool_call_id='actual-call'")
    assert query(memory,action='read',scope='workspace',memory_id=item)['items'][0]['sources'][0]['available']
    with storage.connection:
        storage.connection.execute(f'UPDATE naiyue_memory_sources SET {column}=?',(value,))
    source=query(memory,action='read',scope='workspace',memory_id=item)['items'][0]['sources'][0]
    assert not source['available'] and 'quote' not in source


def test_explicit_history_search_does_not_fold_different_lifecycle_or_expiry(memory):
    storage,_,identity,_=memory
    for number,changes in enumerate([{}, {'status':'candidate'}, {'status':'inactive'},
                                    {'expires_at':1900000000.0}],1):
        seed(storage.connection,number,workspace_id=identity,**changes)
    result=query(memory,action='search',scope='workspace',query='测试环境',status='all')
    assert len(result['items'])==4 and all(not row['duplicate_ids'] for row in result['items'])
