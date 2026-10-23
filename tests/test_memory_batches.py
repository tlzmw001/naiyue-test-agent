"""Bounded saved-source batches and untrusted model proposals use real SQLite."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, message_to_dict
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent.application.conversation.chat_history import ChatUpdate
from naiyue_test_agent.application.memory.memory_queries import MemoryReadError
from naiyue_test_agent.storage.project_store import ProjectStore


@pytest.fixture
def archive(tmp_path):
    with ProjectStore(tmp_path / 'sessions.sqlite') as store:
        store.sessions.bind_workspace('11111111111111111111111111111111', tmp_path)
        store.sessions.create('session', dict(thread_id='root-thread', model_id='A', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        yield store


def save(store, messages, *, thread='root-thread', occurred_at=1000.):
    head = next((h for h in store.conversation.chat_heads('session') if h['thread_id'] == thread), None)
    records = []
    for message in messages:
        body = json.dumps(message_to_dict(message), ensure_ascii=False)
        records.append(dict(message_id=message.id, body_json=body, body_hash=hashlib.sha256(body.encode()).hexdigest(),
            kind='context_summary' if message.additional_kwargs.get('naiyue_compaction_summary') else 'message',
            source_checkpoint_id='checkpoint', message_type=message.type, parent_message_id=None,
            reply_id='reply', occurred_at=occurred_at))
    store.conversation.save_chat_updates('session', [ChatUpdate(thread, '', 'root', head['revision'] if head else 0,
        'checkpoint', tuple(records))])


def batch(store, **changes):
    from naiyue_test_agent.application.memory.memory_batches import build_batch
    return store.memory.build_batch(**(dict(session_id='session', thread_id='root-thread', conversation_revision=0,
        start=[0, 0], upper=100, token_budget=16000, source_chars=4096) | changes))


def settings(**changes):
    return SimpleNamespace(**(dict(overview_chars=1200, max_proposals=8) | changes))


def result(**changes):
    return dict(overview=dict(goal='完成测试', progress='测试通过', blockers='', next_step='检查报告'), proposals=[]) | changes


def proposed(**changes):
    return dict(kind='context', title='测试环境', body='使用.venv', subject='pytest环境',
        basis='user_declaration', intent='activate', reason='用户明确约定',
        sources=[dict(source_key='1:0', quote='使用.venv')]) | changes


def test_batch_only_returns_visible_saved_root_sources_and_real_metadata(archive):
    save(archive, [SystemMessage(id='system', content='HIDDEN-SYSTEM'),
        HumanMessage(id='human', content='使用.venv', additional_kwargs={'naiyue_human_input': True}),
        AIMessage(id='answer', content=[dict(type='text', text='已完成'),
            dict(type='reasoning', reasoning='HIDDEN-REASONING'), dict(type='image_url', image_url='HIDDEN-IMAGE')],
            additional_kwargs={'reasoning_content': 'HIDDEN-ADDITIONAL'},
            tool_calls=[dict(id='call', name='read', args={'path': 'HIDDEN-ARGS'})]),
        ToolMessage(id='tool', content='工具成功', tool_call_id='call', name='read', status='success',
            artifact={'body': 'HIDDEN-ARTIFACT'}),
        AIMessage(id='summary', content='HIDDEN-SUMMARY', additional_kwargs={'naiyue_compaction_summary': True}),
        HumanMessage(id='carrier', content='HIDDEN-CARRIER', additional_kwargs={'naiyue_image_carrier': True})])
    save(archive, [HumanMessage(id='child', content='HIDDEN-CHILD')], thread='subagent:child')
    actual = batch(archive, upper=6)
    assert actual['complete'] and actual['next_position'] == [7, 0]
    assert [s['message_id'] for s in actual['sources']] == ['human', 'answer', 'tool']
    assert [s['role'] for s in actual['sources']] == ['human', 'ai', 'tool']
    assert actual['sources'][-1]['tool_call_id'] == 'call'
    assert actual['sources'][0]['reply_id'] == 'reply' and actual['sources'][0]['occurred_at'] == 1000.
    assert 'HIDDEN-' not in json.dumps(actual, ensure_ascii=False)


def test_long_source_is_split_by_actual_envelope_tokens_without_losing_text(archive):
    text = '测🧪试\x00' * 1000
    save(archive, [HumanMessage(id='long', content=text)])
    position, chunks = [0, 0], []
    for _ in range(200):
        actual = batch(archive, upper=1, start=position, token_budget=400, source_chars=4096)
        assert count_tokens_approximately([HumanMessage(content=json.dumps(actual, ensure_ascii=False))]) <= 400
        assert actual['next_position'] != position
        for source in actual['sources']:
            assert len(source['text']) <= 4096
            assert source['text'] == text[source['offset']:source['offset'] + len(source['text'])]
            assert source['text_chars'] == len(text)
            chunks.append(source['text'])
        position = actual['next_position']
        if actual['complete']:
            break
    assert actual['complete'] and position == [2, 0] and ''.join(chunks) == text


def test_multiple_long_source_chunks_share_one_batch_and_sensitive_scan(archive, monkeypatch):
    from naiyue_test_agent.application.memory import memory_batches
    text = '测试🧪正文' * 4000
    save(archive, [HumanMessage(id='long', content=text)])
    original, checks = memory_batches.SECRET, []
    class Checked:
        def search(self, value):
            checks.append(len(value))
            return original.search(value)
    monkeypatch.setattr(memory_batches, 'SECRET', Checked())
    actual = batch(archive, upper=1, token_budget=16000, source_chars=4096)
    assert actual['complete'] and actual['next_position'] == [2, 0]
    assert len(actual['sources']) > 3 and ''.join(s['text'] for s in actual['sources']) == text
    assert all(0 < len(s['text']) <= 4096 for s in actual['sources'])
    assert len({s['source_key'] for s in actual['sources']}) == len(actual['sources'])
    assert checks == [len(text)]
    assert count_tokens_approximately([HumanMessage(content=json.dumps(actual, ensure_ascii=False))]) <= 16000


def test_secret_source_and_unknown_time_are_skipped_before_any_chunk_exposure(archive):
    private = 'SYNTHETIC_NOT_A_CREDENTIAL'
    save(archive, [HumanMessage(id='sensitive', content='normal prefix' * 500 + '\npassword = ' + private),
        HumanMessage(id='safe', content='后续安全内容')])
    save(archive, [HumanMessage(id='old', content='没有来源时间')], occurred_at=None)
    actual = batch(archive, upper=3, source_chars=10)
    assert [s['message_id'] for s in actual['sources']] == ['safe']
    assert actual['complete'] and len(actual['skipped']) == 2
    raw = json.dumps(actual, ensure_ascii=False)
    assert private not in raw and 'normal prefix' not in raw and '没有来源时间' not in raw


@pytest.mark.parametrize('changes', [dict(thread_id='obsolete'), dict(conversation_revision=1),
    dict(start=[-1, 0]), dict(start=[1, -1]), dict(token_budget=0), dict(source_chars=0)])
def test_batch_rejects_stale_branch_and_invalid_positions(archive, changes):
    save(archive, [HumanMessage(id='first', content='visible')])
    with pytest.raises(MemoryReadError):
        batch(archive, **changes)


def test_batch_keeps_frozen_upper_and_makes_progress_when_envelope_cannot_fit(archive):
    save(archive, [HumanMessage(id='first', content='first'), HumanMessage(id='new', content='after upper')])
    actual = batch(archive, upper=1)
    assert [s['text'] for s in actual['sources']] == ['first']
    assert actual['complete'] and actual['next_position'] == [2, 0]
    small = batch(archive, upper=1, token_budget=80)
    assert small['next_position'] == [2, 0] and small['skipped'] and not small['sources']
    assert count_tokens_approximately([HumanMessage(content=json.dumps(small, ensure_ascii=False))]) <= 80


def test_batch_history_version_tracks_source_rewrites_and_paging_bounds(archive):
    save(archive, [HumanMessage(id='first', content='first')])
    first = batch(archive, upper=1)
    assert first['history_version'] == 0
    save(archive, [HumanMessage(id='first', content='changed')])
    updated = batch(archive, upper=1)
    assert updated['history_version'] == 1 and updated['sources'][0]['revision'] == 2
    with pytest.raises(MemoryReadError, match='memory_batch_position_invalid'):
        batch(archive, start=[1, 99], upper=1)


def test_empty_tail_still_obeys_json_envelope_budget(archive):
    upper = 9223372036854775806
    small = dict(sources=[], next_position=[1, 0], complete=False, skipped=[], history_version=0)
    budget = count_tokens_approximately([HumanMessage(content=json.dumps(small, ensure_ascii=False))])
    with pytest.raises(MemoryReadError, match='memory_batch_budget'):
        batch(archive, upper=upper, token_budget=budget)


def test_batch_rejects_foreign_workspace_even_when_thread_matches(archive):
    save(archive, [HumanMessage(id='first', content='visible')])
    with archive.database.connection:
        archive.database.connection.execute('UPDATE naiyue_sessions SET project_id=? WHERE session_id=?',
            ('foreign-project', 'session'))
    with pytest.raises(MemoryReadError, match='memory_source_scope_mismatch'):
        batch(archive)


def test_batch_sqlite_failure_is_diagnostic_and_not_empty(archive, caplog):
    archive.database.connection.close()
    with pytest.raises(MemoryReadError, match='memory_batch_read_failed'):
        batch(archive)
    assert 'memory_batch_read_failed' in caplog.text


def test_parse_accepts_strict_empty_and_many_proposals_independently():
    from naiyue_test_agent.application.memory.memory_batches import parse_model_result, proposal_request
    data = result(proposals=[proposed(), {'action': 'invented'}, proposed(scope='personal')])
    actual = parse_model_result(AIMessage(content=json.dumps(data), response_metadata={'finish_reason': 'stop'}), settings())
    assert actual == data
    source = dict(source_key='1:0', session_id='session', thread_id='root-thread', message_id='human',
        revision=1, conversation_revision=0, role='human', occurred_at=1000., reply_id='r', tool_call_id=None,
        offset=40, text='以后使用.venv运行测试', text_chars=100)
    first = proposal_request(actual['proposals'][0], [source])
    assert first['action'] == 'record' and first['sources'][0]['offset'] == 42
    assert first['sources'][0]['quote'] == '使用.venv' and first['sources'][0]['revision'] == 1
    assert 'occurred_at' not in first['sources'][0] and 'role' not in first['sources'][0]
    with pytest.raises(MemoryReadError):
        proposal_request(actual['proposals'][1], [source])
    assert proposal_request(actual['proposals'][2], [source])['scope'] == 'personal'
    assert parse_model_result(json.dumps(result()), settings()) == result()


@pytest.mark.parametrize('content', ['{', '```json\n{}\n```', '[]',
    '{"overview":{},"proposals":[],"hidden":"raw"}',
    '{"overview":{"goal":"x","progress":"","blockers":"","next_step":""},"proposals":[],"proposals":[]}',
    json.dumps(result(overview=dict(goal=1, progress='', blockers='', next_step=''))),
    json.dumps(result(proposals=[{}] * 9)), json.dumps(result(overview=dict(goal='x' * 1201, progress='', blockers='', next_step='')))])
def test_invalid_top_level_json_never_becomes_partial_authoritative_state(content):
    from naiyue_test_agent.application.memory.memory_batches import parse_model_result
    with pytest.raises(MemoryReadError, match='memory_result_invalid'):
        parse_model_result(content, settings())


@pytest.mark.parametrize('reason', ['length', 'max_tokens'])
def test_valid_looking_json_from_truncated_response_is_rejected(reason):
    from naiyue_test_agent.application.memory.memory_batches import parse_model_result
    response = AIMessage(content=json.dumps(result()), response_metadata={
        'finish_reason' if reason == 'length' else 'stop_reason': reason})
    with pytest.raises(MemoryReadError, match='memory_result_truncated'):
        parse_model_result(response, settings())


def test_parse_ignores_hidden_response_blocks_and_rejects_nonfinite_json():
    from naiyue_test_agent.application.memory.memory_batches import parse_model_result
    response = AIMessage(content=[dict(type='reasoning', reasoning='HIDDEN-RESPONSE'),
        dict(type='text', text=json.dumps(result()))])
    assert parse_model_result(response, settings()) == result()
    for value in ('NaN', 'Infinity', '-Infinity'):
        content = json.dumps(result()).replace('[]', '[' + value + ']')
        with pytest.raises(MemoryReadError, match='memory_result_invalid'):
            parse_model_result(content, settings())


@pytest.mark.parametrize('change', [dict(sources=[dict(source_key='elsewhere', quote='quote')]),
    dict(sources=[dict(source_key='1:0', quote='missing')]),
    dict(sources=[dict(source_key='1:0', quote='quote', offset=0)]), dict(occurred_at=9999),
    dict(action='record', memory_id='a' * 32), dict(action='update'),
    dict(body='x' * 801), dict(aliases=['a'] * 6)])
def test_proposal_cannot_invent_source_identity_offsets_time_or_exceed_contract(change):
    from naiyue_test_agent.application.memory.memory_batches import proposal_request
    source = dict(source_key='1:0', session_id='s', thread_id='t', message_id='m', revision=1,
        conversation_revision=0, offset=30, text='unique quote', text_chars=99)
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        proposal_request(proposed(**change), [source])


def test_update_reuses_expected_version_and_ambiguous_quote_is_refused():
    from naiyue_test_agent.application.memory.memory_batches import proposal_request
    source = dict(source_key='1:0', session_id='s', thread_id='t', message_id='m', revision=2,
        conversation_revision=3, offset=20, text='使用.venv', text_chars=50)
    snapshot = dict(memory_id='b' * 32, version=4, scope='workspace', kind='context', complete=True,
        title='测试环境', reason='用户明确约定', aliases=[], tags=[], gaps=[],
        conditions=dict(machine=None, module=None, target=None))
    request = proposal_request(proposed(memory_id='b' * 32), [source], [snapshot])
    assert request['memory_id'] == 'b' * 32 and request['expected_version'] == 4
    assert request['sources'][0]['revision'] == 2 and request['sources'][0]['conversation_revision'] == 3
    source['text'] = '使用.venv、使用.venv'
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        proposal_request(proposed(), [source])
