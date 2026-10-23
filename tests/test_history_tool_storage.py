"""The model history projection is read-only, bounded and scope/version checked."""

from hashlib import sha256
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, message_to_dict

from naiyue_test_agent.application.chat_history import ChatUpdate
from naiyue_test_agent.application.session_storage import SessionStorage, SessionPersistenceError
from naiyue_test_agent.tools.history import HistoryInput
from naiyue_test_agent.tools.history_settings import HistorySettings


def create(store, identity, root, thread=None):
    store.create(identity, dict(thread_id=thread or identity, model_id='A', workspace_root=root,
                               active_tool_names=['history'], permission_mode='Default'))


def save(store, sid, messages, *, thread=None):
    thread = thread or sid
    head = next((h for h in store.chat_heads(sid) if h['thread_id'] == thread), None)
    facts = []
    for message in messages:
        body = json.dumps(message_to_dict(message), ensure_ascii=False)
        facts.append(dict(message_id=message.id, body_json=body, body_hash=sha256(body.encode()).hexdigest(),
            kind='context_summary' if message.additional_kwargs.get('naiyue_compaction_summary') else 'message',
            source_checkpoint_id='cp', message_type=message.type, parent_message_id=None, reply_id=None))
    store.save_chat_updates(sid, [ChatUpdate(thread, '', 'root', head['revision'] if head else 0,
                                           'cp', tuple(facts))])


@pytest.fixture
def archive(tmp_path):
    with SessionStorage(tmp_path / 'sessions.sqlite') as store:
        create(store, 'caller', tmp_path)
        create(store, 'target', tmp_path)
        yield store, tmp_path


def query(archive, settings=None, **args):
    store, root = archive
    return store.read_history_tool('caller', root, HistoryInput(**args).model_dump(), settings or HistorySettings())


def test_directory_contains_real_metadata_no_fake_overview_and_no_writes(archive):
    store, root = archive
    create(store, 'foreign', root / 'elsewhere')
    store.update_presentation('target', title='订单测试')
    before = store.connection.total_changes
    result = query(archive, action='list_sessions')
    assert [v['session_id'] for v in result['items']] == ['target']
    assert result['items'][0]['title'] == '订单测试'
    assert result['items'][0]['overview'] is None
    assert result['items'][0]['overview_status'] == 'not_generated'
    assert store.connection.total_changes == before


def test_visible_arrays_and_tool_text_exclude_hidden_fields_from_read_and_search(archive):
    store, _ = archive
    save(store, 'target', [HumanMessage(id='h', content='测试虚拟环境'),
        AIMessage(id='a', content=[{'type': 'reasoning', 'reasoning': 'HIDDEN-THINKING'},
            {'type': 'text', 'text': '使用venv环境'}, {'type': 'image_url', 'image_url': {'url': 'HIDDEN-IMAGE'}}],
            additional_kwargs={'reasoning_content': 'HIDDEN-KWARGS'},
            tool_calls=[{'id': 'c', 'name': 'read', 'args': {'path': 'HIDDEN-ARGUMENT'}}]),
        ToolMessage(id='t', content='工具观察完成', tool_call_id='c', name='read',
                    artifact={'password': 'HIDDEN-ARTIFACT'}),
        AIMessage(id='summary', content='HIDDEN-SUMMARY', additional_kwargs={'naiyue_compaction_summary': True}),
        HumanMessage(id='carrier', content='HIDDEN-CARRIER', additional_kwargs={'naiyue_image_carrier': True}),
        SystemMessage(id='system', content='HIDDEN-SYSTEM')])
    result = query(archive, action='read', session_id='target')
    raw = json.dumps(result, ensure_ascii=False)
    assert '使用venv环境' in raw and '工具观察完成' in raw
    assert 'HIDDEN-' not in raw
    assert {v['message_id'] for v in result['items']} == {'h', 'a', 't'}
    assert query(archive, action='search', session_id='target', query='venv')['items'][0]['message_id'] == 'a'
    assert query(archive, action='search', session_id='target', query='HIDDEN')['items'] == []


def test_empty_search_batch_has_a_continuation_and_late_match_excerpt(archive):
    store, _ = archive
    save(store, 'target', [HumanMessage(id='old', content='x' * 10000 + '目标环境venv'),
        AIMessage(id='middle', content='普通回复'), HumanMessage(id='new', content='普通问题')])
    settings = HistorySettings(search_batch_messages=2)
    first = query(archive, settings, action='search', session_id='target', query='目标环境 venv')
    assert first['items'] == [] and first['has_more'] and first['next_cursor']
    second = query(archive, settings, action='search', session_id='target', query='目标环境 venv',
                   cursor=first['next_cursor'])
    assert second['items'][0]['message_id'] == 'old'
    assert '目标环境venv' in second['items'][0]['content']
    assert not second['has_more']


def test_page_cursor_preserves_upper_on_append_and_rejects_rewrite(archive):
    store, _ = archive
    save(store, 'target', [HumanMessage(id=f'h{i}', content=f'message {i}') for i in range(4)])
    first = query(archive, action='read', session_id='target', limit=2)
    save(store, 'target', [HumanMessage(id='h4', content='appended')])
    older = query(archive, action='read', session_id='target', limit=2, cursor=first['next_cursor'])
    assert [v['message_id'] for v in older['items']] == ['h0', 'h1']
    assert older['coverage']['upper'] == first['coverage']['upper'] == 4
    save(store, 'target', [HumanMessage(id='h0', content='rewritten')])
    with pytest.raises(SessionPersistenceError, match='history_cursor_stale'):
        query(archive, action='read', session_id='target', cursor=first['next_cursor'])


def test_visible_text_chunks_reconstruct_unicode_with_output_budget(archive):
    store, _ = archive
    text = '测试🧪' * 3000
    save(store, 'target', [HumanMessage(id='h', content=text)])
    settings = HistorySettings(max_output_bytes=4096)
    offset, chunks = 0, []
    for _ in range(100):
        page = query(archive, settings, action='read', session_id='target', sequence=1,
                     revision=1, conversation_revision=0, offset=offset)
        assert len(json.dumps(page, ensure_ascii=False).encode()) <= 4096
        item, = page['items']
        assert item['content'] and item['offset'] == offset
        chunks.append(item['content'])
        offset = item['next_offset']
        if item['complete']:break
    assert ''.join(chunks) == text
    assert not page['has_more']
    with pytest.raises(SessionPersistenceError, match='history_cursor_stale'):
        query(archive, action='read', session_id='target', sequence=1, revision=2, conversation_revision=0)


def test_cursors_and_targets_cannot_cross_scope_or_query(archive):
    store, root = archive
    create(store, 'foreign', root / 'other')
    save(store, 'target', [HumanMessage(id=f'h{i}', content='query') for i in range(3)])
    token = query(archive, action='search', session_id='target', query='query', limit=1)['next_cursor']
    for args in (dict(action='search', session_id='target', query='changed', cursor=token),
                 dict(action='read', session_id='target', cursor=token),
                 dict(action='read', session_id='target', agent_id='a' * 64),
                 dict(action='read', session_id='foreign')):
        with pytest.raises(SessionPersistenceError):query(archive, **args)
    with pytest.raises(SessionPersistenceError):
        store.read_history_tool('caller', root / 'other', HistoryInput(action='list_sessions').model_dump(), HistorySettings())
    assert query(archive, action='search', session_id='target', query="' OR 1=1 --")['items'] == []


def test_read_checks_conversation_revision_and_reports_missing_archive(archive):
    store, _ = archive
    save(store, 'target', [HumanMessage(id='h', content='saved')])
    store._write('UPDATE naiyue_sessions SET conversation_revision=1 WHERE session_id=?', ('target',))
    with pytest.raises(SessionPersistenceError, match='history_cursor_stale'):
        query(archive, action='read', session_id='target', sequence=1, revision=1, conversation_revision=0)
    create(store, 'cold', archive[1])
    store._write('UPDATE naiyue_sessions SET checkpoint_id=? WHERE session_id=?', ('not-archived', 'cold'))
    result = query(archive, action='read', session_id='cold')
    assert result['items'] == [] and result['coverage']['status'] == 'unprepared'


def test_sqlite_failure_is_diagnostic_not_empty_success(archive, caplog):
    store, _ = archive
    store.connection.close()
    with pytest.raises(SessionPersistenceError, match='history_read_failed'):
        query(archive, action='list_sessions')
    assert 'history_tool_read_failed' in caplog.text


def test_output_budget_does_not_advance_past_unreturned_messages(archive):
    store,_=archive
    save(store,'target',[HumanMessage(id=f'h{i}',content='长文本'*1000) for i in range(7)])
    settings=HistorySettings(max_output_bytes=4096)
    cursor=None;received=[]
    for _ in range(10):
        result=query(archive,settings,action='read',session_id='target',limit=5,cursor=cursor)
        assert len(json.dumps(result,ensure_ascii=False).encode())<=4096
        received.extend(v['message_id'] for v in result['items'])
        if not result['has_more']:break
        assert result['next_cursor'] and result['next_cursor']!=cursor
        cursor=result['next_cursor']
    assert len(received)==7 and set(received)=={f'h{i}' for i in range(7)}


def test_long_query_empty_batches_stay_inside_output_budget(archive):
    store, _ = archive
    save(store, 'target', [HumanMessage(id=f'h{i}', content='普通消息') for i in range(3)])
    settings = HistorySettings(max_output_bytes=4096, search_batch_messages=1)
    cursor, scanned = None, 0
    for _ in range(3):
        result = query(archive, settings, action='search', session_id='target', query='中' * 2000, cursor=cursor)
        assert len(json.dumps(result, ensure_ascii=False).encode()) <= 4096
        assert result['items'] == []
        scanned += result['scanned']
        cursor = result['next_cursor']
    assert scanned == 3 and not result['has_more'] and cursor is None


@pytest.mark.parametrize('array_content', [False, True])
def test_nul_visible_text_preserves_length_search_and_exact_chunk_reconstruction(archive, array_content):
    store, _ = archive
    text = '前文\x00AFTER🧪' * 100
    content = [{'type': 'text', 'text': text[:201]}, text[201:]] if array_content else text
    save(store, 'target', [HumanMessage(id='h', content=content)])
    settings = HistorySettings(read_chunk_chars=57)
    hit, = query(archive, settings, action='search', session_id='target', query='after')['items']
    assert hit['text_chars'] == len(text) and 'AFTER' in hit['content']
    chunks, offset = [], 0
    for _ in range(100):
        result = query(archive, settings, action='read', session_id='target', sequence=1,
                       revision=1, conversation_revision=0, offset=offset)
        item, = result['items']
        assert item['text_chars'] == len(text) and item['content']
        chunks.append(item['content'])
        offset = item['next_offset']
        if item['complete']:break
    assert ''.join(chunks) == text and offset == len(text) and not result['has_more']
