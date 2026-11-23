"""The extraction model supplies meaning; trusted snapshots own write metadata."""

import asyncio
from memory_comparison_helpers import staged_replies
import json
import time

import pytest
from langchain_core.messages import HumanMessage

from memory_comparison_helpers import compile_request
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_async_application import StreamingToolModel
from test_memory_batches import archive, batch, save
from test_memory_batches import evidence_batch
from memory_wiki_helpers import proposed as wiki_proposal
from test_memory_job_runner import fixture, response


def flat(**changes):
    return dict(matter='Python环境',kind='context', subject='Python环境', body='使用.venv',
        basis='user_declaration', intent='activate',
        sources=[dict(evidence_id='E1')]) | changes


def source(**changes):
    return dict(session_id='session', thread_id='root-thread', message_id='human',
        revision=2, conversation_revision=3, offset=20, text='使用.venv') | changes


def old(**changes):
    return dict(memory_id='a' * 32, version=4, scope='personal', kind='context', status='candidate',
        complete=True, title='既有标题', body='使用.venv', conditions=dict(subject='Python环境',
            machine='Mac', module='tests', target='.venv'), basis='inference', gaps=['待用户确认'],
        aliases=['venv'], tags=['python'], reason='旧建议', deadline_at=None) | changes


def test_minimal_flat_record_has_six_meaning_fields_and_trusted_defaults():
    request = compile_request(flat(), evidence_batch(source()))
    assert request['action'] == 'record' and request['scope'] == 'workspace'
    assert request['memory_id'] is request['expected_version'] is None
    assert request['proposal']['title'] == request['proposal']['subject'] == 'Python环境'
    assert request['proposal']['tags'] == request['proposal']['aliases'] == []
    assert request['proposal']['reason'] and request['proposal']['intent'] == 'activate'
    assert request['sources'][0]['offset'] == 20 and request['sources'][0]['revision'] == 2
    assert 'role' not in request['sources'][0] and 'occurred_at' not in request['sources'][0]


def test_personal_record_is_explicit_and_title_reason_remain_optional():
    request = compile_request(flat(scope='personal', title='解释器', reason='明确选择'), evidence_batch(source()))
    assert request['scope'] == 'personal'
    assert request['proposal']['title'] == '解释器' and request['proposal']['reason'] == '明确选择'


def test_update_uses_only_the_sent_snapshot_and_preserves_omitted_metadata():
    snapshot = old()
    request = compile_request(flat(memory_id=snapshot['memory_id']), evidence_batch(source()), [snapshot])
    assert request['action'] == 'update' and request['scope'] == 'personal'
    assert request['expected_version'] == 4
    p = request['proposal']
    assert p['title'] == '既有标题' and p['reason'] == '旧建议'
    assert p['aliases'] == ['venv'] and p['tags'] == ['python'] and p['gaps'] == ['待用户确认']
    assert (p['machine'], p['module'], p['target']) == ('Mac', 'tests', '.venv')
    assert p['basis'] == 'user_declaration' and p['intent'] == 'activate'
    assert snapshot == old()  # Mapping must not mutate the evidence snapshot.


def test_update_explicit_empty_lists_close_gaps_but_null_keeps_old_values():
    request = compile_request(flat(memory_id='a' * 32, title=None, scope=None,
        aliases=[], gaps=[], machine=None, target='new-venv'), evidence_batch(source()), [old()])
    p = request['proposal']
    assert p['gaps'] == p['aliases'] == [] and p['tags'] == ['python']
    assert p['title'] == '既有标题' and p['machine'] == 'Mac' and p['target'] == 'new-venv'


@pytest.mark.parametrize('changes', [dict(action='record'), dict(expected_version=99),
    dict(proposal={}), dict(tags=[]), dict(deadline=1), dict(occurred_at=1),
    dict(body='x' * 801), dict(aliases=['a'] * 6), dict(scope='both'), dict(intent=None)])
def test_internal_control_fields_and_invalid_meaning_are_not_repaired(changes):
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        compile_request(flat(**changes), evidence_batch(source()))


@pytest.mark.parametrize('rows,changes', [([], {}), ([old(complete=False)], {}),
    ([old(), old(scope='workspace')], {}), ([old()], {'scope': 'workspace'}),
    ([old()], {'kind': 'convention'})])
def test_update_cannot_select_hidden_ambiguous_incomplete_or_different_scope_records(rows, changes):
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        compile_request(flat(memory_id='a' * 32, **changes), evidence_batch(source()), rows)


def test_prepare_sends_compact_sources_and_keeps_the_trusted_envelope(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import _prepare
    async def scenario():
        at = time.time() - 10
        save(archive, [HumanMessage(id='human', content='使用.venv',
            additional_kwargs={'naiyue_human_input': True})], occurred_at=at)
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=staged_replies([])))
        try:
            prepared = await _prepare(service, job, job.upper)
            trusted, messages = prepared[:2]
            payload = json.loads(messages[-1].content)
            visible = payload['batch']['units'][0]
            assert visible['evidence_id'] == trusted['units'][0]['evidence_id']
            assert trusted['units'][0]['message_id'] == 'human'
            assert 'message_id' not in visible and 'revision' not in visible
            assert set(payload['batch']) == {'units', 'complete', 'skipped', 'coverage'}
            assert not job.model.seen and visible['partial_after'] is False
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_frozen_update_cannot_overwrite_a_newer_version_in_real_store(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_wiki_protocol import bind_extraction

    async def scenario():
        archive.memory.prepare_wiki(now=time.time())
        save(archive, [HumanMessage(id='human', content='使用.venv',
            additional_kwargs={'naiyue_human_input': True})], occurred_at=time.time() - 10)
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[]))
        try:
            page = dict(topic='working_agreements', title='Python环境', description='')
            initial = wiki_proposal(intent='candidate')
            bound = bind_extraction(initial, batch(archive), 1)
            record = dict(action='record', scope='workspace', destination={'new_page': page},
                          proposal=bound['proposal'], sources=bound['sources'])
            created = service.memory.query_wiki('session', job.project_id, record)['item']
            assert created['status'] == 'candidate'
            save(archive, [HumanMessage(id='confirmation', content='以后使用.venv',
                additional_kwargs={'naiyue_human_input': True})], occurred_at=time.time() - 1)
            bound = bind_extraction(wiki_proposal(sources=[dict(evidence_id='E2')]), batch(archive), 1)
            frozen = dict(action='update', scope='workspace', memory_id=created['memory_id'],
                expected_version=created['version'], sources=bound['sources'],
                change=dict(mode='replace', proposal=bound['proposal']))
            assert frozen['expected_version'] == 1
            service.memory.query_wiki('session', job.project_id, frozen)
            with pytest.raises(MemoryReadError, match='memory_conflict'):
                service.memory.query_wiki('session', job.project_id, frozen)
            row = archive.database.connection.execute('SELECT version,status,body FROM naiyue_memories').fetchone()
            assert tuple(row) == (2, 'active', '使用.venv')
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_wiki_worker_saves_valid_item_independently_of_invalid_proposal(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from test_memory_wiki_worker import finish, read

    async def scenario():
        archive.memory.prepare_wiki(now=time.time())
        save(archive, [HumanMessage(id='human', content='使用.venv',
            additional_kwargs={'naiyue_human_input': True})], occurred_at=time.time() - 1)
        model = StreamingToolModel(replies=[
            response([wiki_proposal(), {'action': 'invented'}]),
            read(dict(action='list_pages')),
            finish(decisions=[dict(relation='add', proposal_ids=['P1'], destination='N1')],
                   page_changes=[dict(ref='N1', topic='working_agreements', title='环境', description='')]),
        ])
        service, job, events = fixture(archive, tmp_path, model)
        try:
            await drive(service, job)
            assert job.saved == job.overview_saved == job.failed == 1
            assert job.result_status == 'partial' and len(model.seen) == 3 and not model.replies
            assert [e.event_type for e in events if e.event_type in ('model_call_started', 'model_call_finished')] == [
                'model_call_started', 'model_call_finished'] * 3
            rows = archive.database.connection.execute('SELECT body, status FROM naiyue_memories').fetchall()
            assert [tuple(row) for row in rows] == [('使用.venv', 'active')]
        finally:
            service.memory.close()
    asyncio.run(scenario())
