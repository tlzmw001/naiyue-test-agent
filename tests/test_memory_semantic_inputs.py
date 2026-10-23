"""Candidate context is bounded and source-local without changing lexical search."""

import asyncio
import json
import time
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent.application.memory import memory_overview_store
from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.application.memory.memory_jobs import MemoryJob, MemoryJobs
from naiyue_test_agent.application.memory.memory_job_runner import _prepare, _related
from naiyue_test_agent.storage.session_storage import SessionStorage
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.model_access import ModelCatalog
from naiyue_test_agent.tools.memory import MemoryInput
from scripts.probe_memory_semantics import load_cases, seed_candidate, seed_sources


@pytest.fixture
def inputs(tmp_path):
    with SessionStorage(tmp_path / 'sessions.sqlite') as storage:
        storage.bind_workspace(str(uuid4()), tmp_path)
        storage.create('probe', dict(thread_id='probe', model_id='offline', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        settings = MemorySettings(auto_enabled=False)
        memory = MemoryAccess(storage, settings, tmp_path / 'data')
        model = FakeMessagesListChatModel(responses=[], metadata={
            'naiyue_model_limits': dict(context_window_tokens=128000, max_output_tokens=4096)})
        coordinator = MemoryJobs(memory, ModelCatalog('offline', {'offline': model}))
        cases, _ = load_cases()
        at = time.time() - 3600
        candidate = seed_candidate(memory, 'probe', cases, at=at)
        coordinator.prepare('probe', tmp_path, [], model)
        facts = seed_sources(storage, 'probe', cases[10]['sources'], at=at + 10)
        progress = memory_overview_store.ensure(storage, 'probe')
        job = MemoryJob(uuid4().hex, 'probe', 'probe', tmp_path, 'root-run', '', 'offline', model,
            'probe', 0, project_id=storage.get('probe')['project_id'], deadline=time.monotonic() + 300,
            start=(progress['next_sequence'], progress['next_offset']), upper=facts['upper'])
        try:
            yield coordinator, job, candidate, cases, at
        finally:
            asyncio.run(coordinator.aclose())
            memory.close()


def prepared(inputs):
    service, job, *_ = inputs
    batch, messages, _, _, frozen, _ = asyncio.run(_prepare(service, job, job.upper))
    assert all('version' in row for row in frozen)
    assert job.model.i == 0  # This path never invokes the model.
    return batch, messages, json.loads(messages[-1].content)


def test_e11_current_production_input_contains_old_id_complete_gap_and_keeps_version_server_side(inputs):
    service, job, candidate, *_ = inputs
    batch, messages, payload = prepared(inputs)
    query = ' '.join(s['text'] for s in batch['sources'] if s['role'] == 'human')
    lexical = MemoryInput(action='search', status='candidate', query=query).model_dump(mode='json')
    assert service.memory.query('probe', job.project_id, lexical)['items'] == []
    row, = payload['related_memories']
    assert row['memory_id'] == candidate['actual_memory_id'] and 'version' not in row
    frozen = asyncio.run(_prepare(service, job, job.upper))[4]
    assert frozen[0]['version'] == 2
    assert row['status'] == 'candidate' and row['gaps'] == ['缺持久选择']
    assert row['complete'] and row['body'] == inputs[3][4]['sources'][0]['text']
    assert row['basis'] == 'inference' and row['reason']
    assert 'sources' not in row and 'expected' not in payload
    assert count_tokens_approximately(messages) <= service.settings.max_input_tokens


def test_latest_page_does_not_add_another_session_candidate(inputs):
    service, job, own, cases, at = inputs
    service.storage.create('other', dict(thread_id='other', model_id='offline', workspace_root=job.root,
        active_tool_names=['memory'], permission_mode='Default'))
    foreign = seed_candidate(service.memory, 'other', cases, at=at + 20)
    _, _, payload = prepared(inputs)
    ids = {row['memory_id'] for row in payload['related_memories']}
    assert own['actual_memory_id'] in ids and foreign['actual_memory_id'] not in ids


@pytest.mark.parametrize('failure', ['changed', 'other_thread', 'truncated', 'missing'])
def test_latest_candidate_requires_all_available_current_thread_sources(inputs, failure):
    service, job, own, _, at = inputs
    storage = service.storage
    old_id = 'probe-e11-prior-assistant'
    if failure == 'changed':
        seed_sources(storage, 'probe', [dict(id=old_id, role='assistant', text='此旧来源已经更正。')], at=at + 20)
    elif failure == 'other_thread':
        from naiyue_test_agent.application.conversation.chat_history import ChatUpdate
        saved = storage._read('SELECT * FROM naiyue_chat_messages WHERE message_id=?', (old_id,))[0]
        message = {key: saved[key] for key in ('message_id', 'body_json', 'body_hash', 'kind',
            'source_checkpoint_id', 'message_type', 'parent_message_id', 'reply_id', 'occurred_at')}
        storage.save_chat_updates('probe', [ChatUpdate('other-thread', '', 'root', 0, 'other', (message,))])
        with storage.connection:
            storage.connection.execute('UPDATE naiyue_memory_sources SET thread_id=? WHERE memory_id=?',
                ('other-thread', own['actual_memory_id']))
    elif failure == 'truncated':
        service.settings = service.memory.settings = MemorySettings(auto_enabled=False, source_limit=1)
        text = '补充旧候选的有限描述，仍待确认。'
        seed_sources(storage, 'probe', [dict(id='extra-source', role='assistant', text=text)], at=at + 20)
        proposal = dict(kind='procedure', title='自动化用例编排', body=inputs[3][4]['sources'][0]['text'],
            subject='自动化用例编排', basis='inference', intent='candidate', gaps=['缺持久选择'], reason='仍待确认')
        request = MemoryInput(action='update', scope='workspace', memory_id=own['actual_memory_id'], expected_version=2,
            proposal=proposal, sources=[dict(session_id='probe', thread_id='probe', message_id='extra-source',
                revision=1, conversation_revision=0, quote=text, offset=0)]).model_dump(mode='json')
        service.memory.query('probe', job.project_id, request)
    else:
        with storage.connection:
            storage.connection.execute('DELETE FROM naiyue_memory_sources WHERE memory_id=?', (own['actual_memory_id'],))
    latest = MemoryInput(action='list', status='candidate', limit=8).model_dump(mode='json')
    listed, = service.memory.query('probe', job.project_id, latest)['items']
    if failure == 'other_thread':
        assert all(source['available'] for source in listed['sources'])
        assert all(source['thread_id'] == 'other-thread' for source in listed['sources'])
    elif failure == 'truncated':
        assert listed['sources_truncated']
    elif failure == 'changed':
        assert not all(source['available'] for source in listed['sources'])
    else:
        assert listed['sources'] == []
    assert prepared(inputs)[2]['related_memories'] == []


@pytest.mark.parametrize('denied', ['list', 'read'])
def test_latest_list_and_full_read_each_obey_complete_permission_request(inputs, denied, monkeypatch):
    service, job, *_ = inputs
    checks, calls = [], []
    def allowed(request):
        checks.append(request)
        return request['action'] != denied
    service.allowed = allowed
    original = service.memory.query
    def query(sid, root, request):
        calls.append(request)
        return original(sid, root, request)
    monkeypatch.setattr(service.memory, 'query', query)
    assert prepared(inputs)[2]['related_memories'] == []
    assert any(request['action'] == denied for request in checks)
    assert not any(request['action'] == denied for request in calls)
    for request in checks:
        assert MemoryInput(**request).model_dump(mode='json') == request


def test_search_hit_is_deduplicated_against_latest_page_and_keeps_one_read_per_offset(inputs, monkeypatch):
    service, job, candidate, *_ = inputs
    service.settings = service.memory.settings = MemorySettings(auto_enabled=False, read_chunk_chars=8)
    calls, original = [], service.memory.query
    def query(sid, root, request):
        calls.append(request)
        return original(sid, root, request)
    monkeypatch.setattr(service.memory, 'query', query)
    rows = asyncio.run(_related(service, job, dict(sources=[dict(role='human', text='自动化用例编排')])))
    assert [row['memory_id'] for row in rows] == [candidate['actual_memory_id']]
    reads = [request for request in calls if request['action'] == 'read']
    assert len(reads) > 1 and len(reads) == len({r['offset'] for r in reads})
    assert all(request['version'] == 2 for request in reads)


def test_candidate_fallback_uses_only_one_bounded_page_and_never_exceeds_count(inputs, monkeypatch):
    service, job, own, *_ = inputs
    service.settings = service.memory.settings = MemorySettings(auto_enabled=False, related_limit=1)
    calls, original = [], service.memory.query
    def query(sid, root, request):
        calls.append(request)
        return original(sid, root, request)
    monkeypatch.setattr(service.memory, 'query', query)
    rows = prepared(inputs)[2]['related_memories']
    assert [row['memory_id'] for row in rows] == [own['actual_memory_id']]
    lists = [request for request in calls if request['action'] == 'list']
    assert len(lists) == 1 and lists[0]['status'] == 'candidate' and lists[0]['limit'] == 1
    assert lists[0]['cursor'] is None


def test_stale_full_read_is_not_retried_at_a_new_version(inputs, monkeypatch):
    service, job, own, *_ = inputs
    original, changed = service.memory.query, []
    def query(sid, root, request):
        if request['action'] == 'read' and not changed:
            changed.append(True)
            with service.storage.connection:
                service.storage.connection.execute('UPDATE naiyue_memories SET version=version+1 WHERE memory_id=?',
                    (own['actual_memory_id'],))
        return original(sid, root, request)
    monkeypatch.setattr(service.memory, 'query', query)
    assert prepared(inputs)[2]['related_memories'] == [] and changed


def test_input_budget_can_drop_optional_candidate_but_keeps_valid_primary_sources(inputs):
    service, job, own, cases, at = inputs
    service.settings = service.memory.settings = MemorySettings(auto_enabled=False, related_limit=0)
    original_sources, messages, _ = prepared(inputs)
    cap = count_tokens_approximately(messages) + 32
    with service.storage.connection:
        service.storage.connection.execute('UPDATE naiyue_memories SET body=? WHERE memory_id=?',
            ('bounded optional context ' * 100, own['actual_memory_id']))
    service.settings = service.memory.settings = MemorySettings(auto_enabled=False, related_limit=8, max_input_tokens=cap)
    batch, messages, payload = prepared(inputs)
    assert payload['related_memories'] == []
    assert {s['message_id'] for s in batch['sources']} == {s['message_id'] for s in original_sources['sources']}
    assert count_tokens_approximately(messages) <= cap


def test_existing_cross_session_search_hit_is_not_filtered_by_latest_page_rules(inputs):
    service, job, own, cases, at = inputs
    service.storage.create('other', dict(thread_id='other', model_id='offline', workspace_root=job.root,
        active_tool_names=['memory'], permission_mode='Default'))
    foreign = seed_candidate(service.memory, 'other', cases, at=at + 20)
    service.allowed = lambda request: request['action'] != 'list'
    rows = asyncio.run(_related(service, job, dict(sources=[dict(role='human', text='自动化用例编排')])))
    assert {row['memory_id'] for row in rows} == {own['actual_memory_id'], foreign['actual_memory_id']}


def test_ineligible_latest_page_is_not_followed_to_find_an_older_local_candidate(inputs, monkeypatch):
    service, job, _, cases, at = inputs
    service.settings = service.memory.settings = MemorySettings(auto_enabled=False, related_limit=1)
    service.storage.create('other', dict(thread_id='other', model_id='offline', workspace_root=job.root,
        active_tool_names=['memory'], permission_mode='Default'))
    seed_candidate(service.memory, 'other', cases, at=at + 20)
    calls, original = [], service.memory.query
    def query(sid, root, request):
        calls.append(request)
        return original(sid, root, request)
    monkeypatch.setattr(service.memory, 'query', query)
    assert prepared(inputs)[2]['related_memories'] == []
    lists = [request for request in calls if request['action'] == 'list']
    assert len(lists) == 1 and lists[0]['limit'] == 1 and lists[0]['cursor'] is None


def test_newly_unavailable_source_is_rechecked_during_full_read(inputs, monkeypatch):
    service, job, _, _, at = inputs
    service.settings = service.memory.settings = MemorySettings(auto_enabled=False, read_chunk_chars=8)
    calls, original = [], service.memory.query
    def query(sid, root, request):
        if request['action'] == 'read':
            calls.append(request)
            if len(calls) == 2:
                seed_sources(service.storage, 'probe', [dict(id='probe-e11-prior-assistant',
                    role='assistant', text='旧来源在读取期间已改写。')], at=at + 20)
        return original(sid, root, request)
    monkeypatch.setattr(service.memory, 'query', query)
    assert prepared(inputs)[2]['related_memories'] == [] and len(calls) == 2


def test_search_plus_latest_candidates_share_the_same_candidate_count_limit(inputs, monkeypatch):
    service, job, own, cases, at = inputs
    service.settings = service.memory.settings = MemorySettings(auto_enabled=False, related_limit=2)
    quote = cases[4]['sources'][0]['text']
    reference = dict(session_id='probe', thread_id='probe', message_id='probe-e11-prior-assistant',
        revision=1, conversation_revision=0, quote=quote, offset=0)
    additional = []
    for index in range(2):
        proposal = dict(kind='context', title=f'其他候选事项{index}', body='等待网络策略确认',
            subject=f'候选网络事项{index}', basis='inference', intent='candidate', gaps=['待确认'], reason='有限测试前置')
        request = MemoryInput(action='record', scope='workspace', proposal=proposal, sources=[reference]).model_dump(mode='json')
        additional.append(service.memory.query('probe', job.project_id, request, now=at + 100 + index)['item']['memory_id'])
    calls, original = [], service.memory.query
    def query(sid, root, request):
        calls.append(request)
        return original(sid, root, request)
    monkeypatch.setattr(service.memory, 'query', query)
    rows = asyncio.run(_related(service, job, dict(sources=[dict(role='human', text='自动化用例编排')])))
    assert len(rows) == 2
    assert {row['memory_id'] for row in rows} == {own['actual_memory_id'], additional[-1]}
    lists = [request for request in calls if request['action'] == 'list']
    assert len(lists) == 1 and lists[0]['limit'] == 2 and lists[0]['cursor'] is None
