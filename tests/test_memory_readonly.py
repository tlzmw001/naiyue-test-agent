"""Product memory queries use bounded, versioned authority without a model."""

import json
from uuid import uuid4

import pytest

from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.application.memory.memory_access import MemoryAccess, MemoryReadError
from naiyue_test_agent.storage.database import Database
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
    with ProjectStore(tmp_path / 'sessions.sqlite') as storage:
        storage.sessions.bind_workspace(identity, tmp_path)
        storage.sessions.create('caller', dict(thread_id='caller', model_id='A', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        access = MemoryAccess(storage, MemorySettings(), tmp_path / 'data')
        try:yield storage, access, identity, tmp_path
        finally:access.close()


def query(memory, **arguments):
    _, access, _, root = memory
    return access.query('caller', access.storage.sessions.get('caller')['project_id'], MemoryInput(**arguments).model_dump(), now=2000000000.0)


@pytest.fixture
def wiki_memory(memory):
    memory[0].memory.prepare_wiki(now=2000000000.)
    return memory


def seed_wiki(connection,number,*,scope='workspace',workspace_id=None,title='测试环境',body='使用项目venv',**changes):
    from memory_wiki_helpers import record
    from naiyue_test_agent.storage.memory_sql.memory_wiki_writes import WikiWriter
    page=WikiWriter.new_page('working_agreements',title,'只读查询测试')
    fields=dict(memory_id=f'{number:032x}',title=title,body=body,created_at=1000000000.,
        updated_at=1000000000.,evidence_at=1000000000.,expires_at=2000000000.+86400)|changes
    plan=record(page['page_id'],scope=scope,workspace_id=workspace_id,**fields)
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        writer=WikiWriter(connection,scope,workspace_id,now=1000000000.,capacity=30)
        writer.apply(pages=[page],memories=[plan])
    return plan['row']['memory_id']


def query_wiki(memory,**arguments):
    return memory[1].query_wiki('caller',memory[2],arguments,now=2000000000.)


def entries(result):
    from test_memory_wiki_pagination import decode_content
    return decode_content(result)['entries']


def add_wiki_source(connection,item,workspace,*,quote,message='h',role='human',offset=0):
    # Replace only the synthetic seed provenance; actual sources below exercise
    # archive visibility, revision and tool-call pairing.
    with connection:
        connection.execute("DELETE FROM naiyue_memory_sources WHERE memory_id=? AND session_id='s' AND source_workspace_id='origin'",(item,))
        connection.execute('''INSERT INTO naiyue_memory_sources(memory_id,memory_version,source_workspace_id,
            session_id,thread_id,message_id,revision,conversation_revision,role,occurred_at,quote,start_offset,end_offset,supports_scope)
            VALUES(?,1,?,'caller','caller',?,1,0,?,1000000000,?,?,?,0)''',
            (item,workspace,message,role,quote,offset,offset+len(quote)))


def test_empty_query_is_readonly_and_personal_is_lazy(wiki_memory):
    storage, access, _, root = wiki_memory
    assert not (root / 'data/personal/memory.sqlite').exists()
    before = storage.database.connection.total_changes
    assert entries(query_wiki(wiki_memory, action='list', scope='workspace')) == []
    assert storage.database.connection.total_changes == before
    assert not (root / 'data/personal/memory.sqlite').exists()
    assert entries(query_wiki(wiki_memory, action='list')) == []
    assert (root / 'data/personal/memory.sqlite').exists()

def test_scope_status_expiry_conditions_and_reads_never_renew(wiki_memory):
    storage, _, identity, _ = wiki_memory
    c = storage.database.connection
    valid = seed_wiki(c, 1, workspace_id=identity, machine='windows')
    for number, changes in enumerate([{'status': 'candidate'}, {'status': 'inactive'}, {'status': 'forgotten'}, {'expires_at': 2000000000.0}], 2):
        seed_wiki(c, number, **{'workspace_id': identity, **changes})
    with pytest.raises(MemoryReadError,match='memory_scope_mismatch'):
        seed_wiki(c,99,workspace_id=str(uuid4()))
    assert c.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==5
    result = query_wiki(wiki_memory, action='list', scope='workspace')
    assert [r['memory_id'] for r in entries(result)] == [valid]
    assert entries(result)[0]['needs_verification']
    assert entries(query_wiki(wiki_memory, action='list', scope='workspace', machine='mac')) == []
    assert len(entries(query_wiki(wiki_memory, action='list', scope='workspace', status='candidate'))) == 1
    assert len(entries(query_wiki(wiki_memory, action='list', scope='workspace', status='expired'))) == 1
    assert len(entries(query_wiki(wiki_memory, action='list', scope='workspace', status='all'))) == 4
    before = c.total_changes
    query_wiki(wiki_memory, action='read', scope='workspace', memory_id=valid, version=1)
    assert c.total_changes == before

def test_cascade_and_technical_anchors_reject_weak_or_different_environment(wiki_memory):
    storage, _, identity, _ = wiki_memory
    c = storage.database.connection
    seed_wiki(c, 1, workspace_id=identity, title='测试环境', body='测试执行前确认解释器')
    exact = seed_wiki(c, 2, workspace_id=identity, title='pytest测试类组织', body='使用TestOrder')
    assert [r['memory_id'] for r in entries(query_wiki(wiki_memory, action='search', scope='workspace', query='测试类'))] == [exact]
    expected = seed_wiki(c, 3, workspace_id=identity, title='用例生成', body='TC-01使用.venv')
    seed_wiki(c, 4, workspace_id=identity, title='用例生成', body='TC-02使用.venv')
    result = query_wiki(wiki_memory, action='search', scope='workspace', query='用例生成 TC-01')
    assert [r['memory_id'] for r in entries(result)] == [expected]
    assert entries(query_wiki(wiki_memory, action='search', scope='workspace', query='不存在的主题')) == []

def test_configurable_k_deduplicates_after_top_k_without_refill(wiki_memory):
    storage, access, identity, _ = wiki_memory
    access.settings = MemorySettings(default_limit=2, max_limit=3)
    for number in range(1, 5):
        seed_wiki(storage.database.connection, number, workspace_id=identity)
    result = query_wiki(wiki_memory, action='search', scope='workspace', query='测试环境')
    assert len(entries(result)) == 1 and len(entries(result)[0]['duplicate_ids']) == 1
    assert result['truncated']
    assert len(entries(query_wiki(wiki_memory, action='search', scope='workspace', query='测试环境', limit=3))[0]['duplicate_ids']) == 2
    with pytest.raises(MemoryReadError, match='memory_invalid_limit'):
        query_wiki(wiki_memory, action='list', scope='workspace', limit=4)

def test_current_version_and_digest_control_stale_index(wiki_memory):
    storage, _, identity, _ = wiki_memory
    item = seed_wiki(storage.database.connection, 1, workspace_id=identity, body='old-needle')
    with storage.database.connection:
        storage.database.connection.execute('UPDATE naiyue_memories SET body=?,version=2 WHERE memory_id=?', ('new-needle', item))
    assert entries(query_wiki(wiki_memory, action='search', scope='workspace', query='old-needle')) == []
    assert entries(query_wiki(wiki_memory, action='search', scope='workspace', query='new-needle'))[0]['version'] == 2
    with pytest.raises(MemoryReadError, match='memory_version_changed'):
        query_wiki(wiki_memory, action='read', scope='workspace', memory_id=item, version=1)
    with storage.database.connection:
        storage.database.connection.execute('UPDATE naiyue_memory_search SET memory_version=1')
    assert entries(query_wiki(wiki_memory, action='search', scope='workspace', query='new-needle')) == []

def test_detail_nul_unicode_budget_and_cursor_scope(wiki_memory):
    storage, access, identity, _ = wiki_memory
    text = '前文\x00正文🧪' * 100
    item = seed_wiki(storage.database.connection, 1, workspace_id=identity, body=text)
    seed_wiki(storage.database.connection, 2, workspace_id=identity)
    access.settings = MemorySettings(wiki_read_tokens=700, max_output_bytes=4096)
    chunks, offset = ([], 0)
    for _ in range(30):
        result = query_wiki(wiki_memory, action='read', scope='workspace', memory_id=item, version=1, offset=offset)
        row, = entries(result)
        assert len(json.dumps(result, ensure_ascii=False).encode()) <= 4096
        assert result['budget']['estimated_tokens']<=700
        chunks.append(row['body'])
        offset = result['next_offset']
        if row['entry_complete']:
            break
    assert len(chunks)>1 and ''.join(chunks) == text and offset == len(text)
    first = query_wiki(wiki_memory, action='list', scope='workspace', limit=1)
    assert first['has_more'] and first['next_cursor']
    second = query_wiki(wiki_memory, action='list', scope='workspace', limit=1, cursor=first['next_cursor'])
    assert entries(first)[0]['memory_id'] != entries(second)[0]['memory_id']
    with pytest.raises(MemoryReadError, match='memory_cursor_stale'):
        query_wiki(wiki_memory, action='list', scope='both', limit=1, cursor=first['next_cursor'])

def test_personal_shared_connection_and_foreign_sources_do_not_read_other_project(wiki_memory):
    storage, _, _, root = wiki_memory
    path = root / 'data/personal/memory.sqlite'
    with Database.open_personal(path, MemorySettings()) as personal:
        from naiyue_test_agent.storage.memory import MemoryRepo
        MemoryRepo(personal).prepare_wiki(now=1000000000.)
        item = seed_wiki(personal.connection, 1, scope='personal')
    result = query_wiki(wiki_memory, action='search', query='测试环境')
    assert entries(result)[0]['scope'] == 'personal' and entries(result)[0]['memory_id'] == item
    assert storage.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0] == 0

def add_source(connection, item, workspace, *, quote, message='h', role='human', offset=0):
    with connection:
        connection.execute('''INSERT INTO naiyue_memory_sources(memory_id,memory_version,source_workspace_id,
            session_id,thread_id,message_id,revision,conversation_revision,role,occurred_at,quote,start_offset,end_offset)
            VALUES(?,1,?,'caller','caller',?,1,0,?,1000000000,?,?,?)''',
            (item, workspace, message, role, quote, offset, offset + len(quote)))


def test_sources_only_quote_current_visible_archive_and_foreign_reference_is_metadata(wiki_memory):
    from langchain_core.messages import HumanMessage, AIMessage
    from test_history_tool_storage import save
    storage, access, identity, _ = wiki_memory
    save(storage, 'caller', [HumanMessage(id='h', content='明确使用.venv'), AIMessage(id='a', content=[{'type': 'reasoning', 'reasoning': 'HIDDEN-SOURCE'}, {'type': 'text', 'text': '可见结果'}])])
    item = seed_wiki(storage.database.connection, 1, workspace_id=identity)
    add_wiki_source(storage.database.connection, item, identity, quote='使用.venv', offset=2)
    add_wiki_source(storage.database.connection, item, identity, quote='HIDDEN-SOURCE', message='a', role='ai')
    result = query_wiki(wiki_memory, action='read', scope='workspace', memory_id=item, version=1)
    sources = entries(result)[0]['sources']
    assert sources[0]['available'] and sources[0]['quote'] == '使用.venv'
    assert not sources[1]['available'] and 'quote' not in sources[1]
    assert 'HIDDEN-SOURCE' not in json.dumps(result, ensure_ascii=False)
    query_wiki(wiki_memory, action='list', scope='personal')
    personal = access._personal_store.database.connection
    other = seed_wiki(personal, 2, scope='personal')
    add_wiki_source(personal, other, str(uuid4()), quote='FOREIGN-SECRET')
    source, = entries(query_wiki(wiki_memory, action='read', scope='personal', memory_id=other))[0]['sources']
    assert not source['available'] and source['reason'] == 'other_workspace' and ('quote' not in source)
    save(storage, 'caller', [HumanMessage(id='h', content='来源已改写')])
    assert not entries(query_wiki(wiki_memory, action='read', scope='workspace', memory_id=item))[0]['sources'][0]['available']

def test_expansion_anchors_cannot_override_original_and_fallback_is_configurable(wiki_memory):
    storage, access, identity, _ = wiki_memory
    expected = seed_wiki(storage.database.connection, 1, workspace_id=identity, title='代理', body='10.0.0.1走代理')
    seed_wiki(storage.database.connection, 2, workspace_id=identity, title='代理', body='10.0.0.2走代理')
    result = query_wiki(wiki_memory, action='search', scope='workspace', query='代理 10.0.0.1', expansions=['代理 10.0.0.2'])
    assert [r['memory_id'] for r in entries(result)] == [expected]
    reversed_item = seed_wiki(storage.database.connection, 3, workspace_id=identity, title='生成用例', body='流程')
    assert entries(query_wiki(wiki_memory, action='search', scope='workspace', query='用例生成'))[0]['memory_id'] == reversed_item
    access.settings = MemorySettings(fallback_coverage=1.0)
    assert entries(query_wiki(wiki_memory, action='search', scope='workspace', query='用例生成')) == []

def test_exact_folding_preserves_conditions_basis_and_code_whitespace(wiki_memory):
    storage, _, identity, _ = wiki_memory
    for number, changes in enumerate([{}, {'subject': 'other'}, {'basis': 'inference'}, {'body': 'pytest\n    pass'}, {'body': 'pytest\n pass'}], 1):
        seed_wiki(storage.database.connection, number, workspace_id=identity, title='pytest', body=changes.pop('body', 'pytest'), **changes)
    result = query_wiki(wiki_memory, action='search', scope='workspace', query='pytest')
    assert len(entries(result)) == 5 and all((not row['duplicate_ids'] for row in entries(result)))

def test_query_complexity_is_bounded_and_sql_text_is_literal(wiki_memory):
    _, access, _, _ = wiki_memory
    access.settings = MemorySettings(max_query_terms=2)
    with pytest.raises(MemoryReadError, match='memory_query_too_complex'):
        query_wiki(wiki_memory, action='search', scope='workspace', query='alpha beta gamma')
    access.settings = MemorySettings()
    assert entries(query_wiki(wiki_memory, action='search', scope='workspace', query="' OR 1=1 --")) == []

@pytest.mark.parametrize('column,value', [('tool_call_id', 'wrong-call'), ('reply_id', 'wrong-reply')])
def test_source_call_and_reply_ids_must_match_visible_authority(wiki_memory, column, value):
    from langchain_core.messages import ToolMessage
    from test_history_tool_storage import save
    storage, _, identity, _ = wiki_memory
    save(storage, 'caller', [ToolMessage(id='t', tool_call_id='actual-call', content='观察成功')])
    item = seed_wiki(storage.database.connection, 1, workspace_id=identity)
    add_wiki_source(storage.database.connection, item, identity, quote='观察成功', message='t', role='tool')
    with storage.database.connection:
        storage.database.connection.execute("UPDATE naiyue_memory_sources SET tool_call_id='actual-call'")
    assert entries(query_wiki(wiki_memory, action='read', scope='workspace', memory_id=item))[0]['sources'][0]['available']
    with storage.database.connection:
        storage.database.connection.execute(f'UPDATE naiyue_memory_sources SET {column}=?', (value,))
    source = entries(query_wiki(wiki_memory, action='read', scope='workspace', memory_id=item))[0]['sources'][0]
    assert not source['available'] and 'quote' not in source

def test_explicit_history_search_does_not_fold_different_lifecycle_or_expiry(wiki_memory):
    storage, _, identity, _ = wiki_memory
    for number, changes in enumerate([{}, {'status': 'candidate'}, {'status': 'inactive'}, {'expires_at': 1900000000.0}], 1):
        seed_wiki(storage.database.connection, number, workspace_id=identity, **changes)
    result = query_wiki(wiki_memory, action='search', scope='workspace', query='测试环境', status='all')
    assert len(entries(result)) == 4 and all((not row['duplicate_ids'] for row in entries(result)))
