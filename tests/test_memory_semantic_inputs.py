"""A input stays source-local while B discovers candidates through scoped Wiki reads."""

import asyncio
import json
import time
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages.utils import count_tokens_approximately

from naiyue_test_agent.application.memory.memory_access import MemoryAccess
from naiyue_test_agent.application.memory.memory_jobs import MemoryJob, MemoryJobs
from naiyue_test_agent.application.memory.memory_job_runner import _prepare
from naiyue_test_agent.storage.project_store import ProjectStore
from naiyue_test_agent.memory_settings import MemorySettings
from naiyue_test_agent.model_access import ModelCatalog
from scripts.probe_memory_semantics import load_cases, seed_sources
from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.tools.memory_wiki_actions import compile_wiki_request
from test_memory_wiki_pagination import decode_content


TOPIC = 'working_agreements'


def seed_candidate(memory, sid, cases, *, at):
    text = cases[4]['sources'][0]['text']
    message_id = 'probe-e11-prior-assistant'
    seed_sources(memory.storage, sid, [dict(id=message_id, role='user', text=text)], at=at)
    parent = memory.storage.sessions.get(sid)
    ref = dict(session_id=sid, thread_id=parent['thread_id'], message_id=message_id,
               revision=1, conversation_revision=parent['conversation_revision'], quote=text, offset=0)
    proposal = dict(title='自动化用例编排', body=text, subject='自动化用例编排', basis='inference',
                    intent='candidate', gaps=['缺持久选择'], reason='用户来源支持待确认推断')
    scope = dict(scope='workspace', sources=[ref])
    page = dict(topic=TOPIC, title=proposal['title'], description='')
    project = parent['project_id']
    saved = memory.query_wiki(sid, project, dict(action='record', **scope, proposal=proposal,
        destination={'new_page': page}), now=at+1)['item']
    return memory.query_wiki(sid, project, dict(action='update', **scope,
        memory_id=saved['memory_id'], expected_version=saved['version'], change={'mode': 'supplement'}),
        now=at+2)['item']


@pytest.fixture
def inputs(tmp_path):
    with ProjectStore(tmp_path / 'sessions.sqlite') as storage:
        storage.sessions.bind_workspace(str(uuid4()), tmp_path)
        storage.sessions.create('probe', dict(thread_id='probe', model_id='offline', workspace_root=tmp_path,
            active_tool_names=['memory'], permission_mode='Default'))
        settings = MemorySettings(auto_enabled=False)
        memory = MemoryAccess(storage, settings, tmp_path / 'data')
        model = FakeMessagesListChatModel(responses=[], metadata={
            'naiyue_model_limits': dict(context_window_tokens=128000, max_output_tokens=4096)})
        coordinator = MemoryJobs(memory, ModelCatalog('offline', {'offline': model}))
        cases, _ = load_cases()
        at = time.time() - 3600
        storage.memory.prepare_wiki(now=at)
        candidate = seed_candidate(memory, 'probe', cases, at=at)
        coordinator.prepare('probe', tmp_path, [], model)
        facts = seed_sources(storage, 'probe', cases[10]['sources'], at=at + 10)
        progress = storage.memory.ensure('probe')
        job = MemoryJob(uuid4().hex, 'probe', 'probe', tmp_path, 'root-run', '', 'offline', model,
            'probe', 0, project_id=storage.sessions.get('probe')['project_id'], deadline=time.monotonic() + 300,
            start=(progress['next_sequence'], progress['next_offset']), upper=facts['upper'])
        try:
            yield coordinator, job, candidate, cases, at
        finally:
            asyncio.run(coordinator.aclose())
            memory.close()


def prepared(inputs):
    service,job,*_=inputs
    batch,messages,_,_,_=asyncio.run(_prepare(service,job,job.upper))
    assert job.model.i==0
    return batch,messages,json.loads(messages[-1].content)


def directory(inputs, ledger, allowed, **extra):
    service, job, *_ = inputs
    return ledger.read(service.memory, 'probe', job.project_id,
                       dict(action='list_pages', topic=ledger.topic, **extra), allowed)


def read_page(inputs, ledger, page_id, allowed, **extra):
    service, job, *_ = inputs
    return ledger.read(service.memory, 'probe', job.project_id,
                       dict(action='read_page', page_id=page_id, **extra), allowed)


def lookup(inputs, *, allowed=lambda _: True):
    ledger = Ledger('workspace', TOPIC)
    result = directory(inputs, ledger, allowed)
    assert result['complete']
    for page in decode_content(result)['pages']:
        result = read_page(inputs, ledger, page['page_id'], allowed)
        while result['has_more']:
            result = read_page(inputs, ledger, page['page_id'], allowed, cursor=result['next_cursor'])
        assert ledger.require_page(page['page_id'])
    return ledger


def test_extraction_does_not_receive_old_ids_or_gaps(inputs):
    service, _, candidate, *_ = inputs
    _, messages, payload = prepared(inputs)
    assert 'related_memories' not in payload
    assert candidate['memory_id'] not in messages[-1].content
    assert '缺持久选择' not in messages[-1].content
    assert count_tokens_approximately(messages) <= service.settings.max_input_tokens
    row, = lookup(inputs).old.values()
    assert row['version'] == candidate['version'] == 2
    assert '缺持久选择' in row['gaps'] and row['body'] == inputs[3][4]['sources'][0]['text']


def test_directory_finds_accessible_other_session_candidates(inputs):
    service, job, own, cases, at = inputs
    service.storage.sessions.create('other', dict(thread_id='other', model_id='offline', workspace_root=job.root,
        active_tool_names=['memory'], permission_mode='Default'))
    foreign = seed_candidate(service.memory, 'other', cases, at=at+20)
    assert {r['memory_id'] for r in lookup(inputs).old.values()} == {own['memory_id'], foreign['memory_id']}


@pytest.mark.parametrize('failure', ['changed', 'missing', 'other_thread'])
def test_old_source_unavailable_is_explicit_but_does_not_hide_old_body(inputs, failure):
    service, job, own, _, at = inputs
    if failure == 'changed':
        seed_sources(service.storage, 'probe',
            [dict(id='probe-e11-prior-assistant', role='user', text='更正旧来源')], at=at+20)
    else:
        with service.storage.database.transaction(write=True) as connection:
            if failure == 'missing':
                connection.execute('DELETE FROM naiyue_chat_messages WHERE message_id=?', ('probe-e11-prior-assistant',))
            else:
                connection.execute('UPDATE naiyue_memory_sources SET thread_id=? WHERE memory_id=?',
                                   ('missing-thread', own['memory_id']))
    ledger = lookup(inputs)
    row, = ledger.old.values()
    assert row['body'] == inputs[3][4]['sources'][0]['text']
    result = ledger.read(service.memory, 'probe', job.project_id,
        dict(action='read', memory_id=own['memory_id'], status='candidate'), lambda _: True)
    entry, = decode_content(result)['entries']
    assert entry['body'] == row['body'] and entry['sources']
    assert all(not s['available'] and 'quote' not in s for s in entry['sources'])


@pytest.mark.parametrize('denied', ['list_pages', 'read_page'])
def test_each_normalized_permission_denial_is_failure_not_empty_result(inputs, denied):
    checks = []

    def allowed(request):
        checks.append(request)
        assert compile_wiki_request({'request': request}) == request
        return request['action'] != denied

    with pytest.raises(MemoryReadError, match='memory_permission_denied'):
        lookup(inputs, allowed=allowed)
    assert checks[-1]['action'] == denied


def test_repeated_page_read_fails_without_losing_complete_entry(inputs):
    service, _, own, *_ = inputs
    service.memory.settings = MemorySettings(auto_enabled=False, read_chunk_chars=8, source_limit=1)
    ledger = lookup(inputs)
    row, = ledger.old.values()
    assert row['version'] == 2 and row['body'] == inputs[3][4]['sources'][0]['text']
    assert len(row['body']) > 8
    with pytest.raises(MemoryReadError, match='memory_read_no_progress'):
        read_page(inputs, ledger, own['page_id'], lambda _: True)
    assert len(ledger.old) == 1


def test_stale_snapshot_is_not_retried(inputs, monkeypatch):
    service, _, own, *_ = inputs
    ledger = Ledger('workspace', TOPIC)
    directory(inputs, ledger, lambda _: True)
    original = service.storage.memory.wiki_view
    calls = []

    def changed(*args, **kwargs):
        calls.append(args[2]['action'])
        view = original(*args, **kwargs)
        return dict(view, catalog_revision=view['catalog_revision'] + 1)

    monkeypatch.setattr(service.storage.memory, 'wiki_view', changed)
    with pytest.raises(MemoryReadError, match='memory_cursor_stale'):
        read_page(inputs, ledger, own['page_id'], lambda _: True)
    assert calls == ['list_topics'] and not ledger.old


def test_unrelated_topic_does_not_fall_back_to_recent_candidate(inputs):
    ledger = Ledger('workspace', 'stable_knowledge')
    result = directory(inputs, ledger, lambda _: True)
    assert decode_content(result)['pages'] == [] and result['complete']
    with pytest.raises(MemoryReadError, match='memory_page_not_discovered'):
        read_page(inputs, ledger, inputs[2]['page_id'], lambda _: True)
    assert not ledger.old


def test_partial_directory_cannot_authorize_new_destination(inputs):
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    service, job, _, cases, at = inputs
    for i in range(8):
        sid = f'other-{i}'
        service.storage.sessions.create(sid, dict(thread_id=sid, model_id='offline', workspace_root=job.root,
            active_tool_names=['memory'], permission_mode='Default'))
        seed_candidate(service.memory, sid, cases, at=at+20+i)
    service.memory.settings = MemorySettings(auto_enabled=False, wiki_read_tokens=900)
    ledger = Ledger('workspace', TOPIC)
    result = directory(inputs, ledger, lambda _: True)
    assert result['has_more'] and TOPIC not in ledger.directories
    before = service.storage.database.connection.total_changes
    finish = dict(step='finish', decisions=[], page_changes=[
        dict(ref='N1', topic=TOPIC, title='不完整扫描', description='')])
    with pytest.raises(MemoryReadError, match='memory_read_incomplete'):
        commit_group(service.memory, 'probe', job.project_id, [(ledger, {}, finish)], lambda _: True)
    assert before == service.storage.database.connection.total_changes


def test_extraction_budget_is_independent_of_old_memory_material(inputs):
    service,job,own,*_=inputs
    first,messages,_=prepared(inputs)
    cap=count_tokens_approximately(messages)+32
    with service.storage.database.connection:
        service.storage.database.connection.execute('UPDATE naiyue_memories SET body=? WHERE memory_id=?',('optional old body '*100,own['memory_id']))
    service.settings=service.memory.settings=MemorySettings(auto_enabled=False,max_input_tokens=cap)
    batch,messages,payload=prepared(inputs)
    assert 'related_memories' not in payload
    assert [u['message_id'] for u in batch['units']]==[u['message_id'] for u in first['units']]
    assert count_tokens_approximately(messages)<=cap
