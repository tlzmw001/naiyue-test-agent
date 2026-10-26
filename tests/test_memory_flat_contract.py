"""The extraction model supplies meaning; trusted snapshots own write metadata."""

import asyncio
import json
import time

import pytest
from langchain_core.messages import HumanMessage

from naiyue_test_agent.application.memory.memory_batches import proposal_request
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_async_application import StreamingToolModel
from test_memory_batches import archive, batch, save
from test_memory_job_runner import fixture, response


def flat(**changes):
    return dict(kind='context', subject='Python环境', body='使用.venv',
        basis='user_declaration', intent='activate',
        sources=[dict(source_key='1:0', quote='使用.venv')]) | changes


def source(**changes):
    return dict(source_key='1:0', session_id='session', thread_id='root-thread', message_id='human',
        revision=2, conversation_revision=3, offset=20, text='使用.venv') | changes


def old(**changes):
    return dict(memory_id='a' * 32, version=4, scope='personal', kind='context', status='candidate',
        complete=True, title='既有标题', body='使用.venv', conditions=dict(subject='Python环境',
            machine='Mac', module='tests', target='.venv'), basis='inference', gaps=['待用户确认'],
        aliases=['venv'], tags=['python'], reason='旧建议', deadline_at=None) | changes


def test_minimal_flat_record_has_six_meaning_fields_and_trusted_defaults():
    request = proposal_request(flat(), [source()])
    assert request['action'] == 'record' and request['scope'] == 'workspace'
    assert request['memory_id'] is request['expected_version'] is None
    assert request['proposal']['title'] == request['proposal']['subject'] == 'Python环境'
    assert request['proposal']['tags'] == request['proposal']['aliases'] == []
    assert request['proposal']['reason'] and request['proposal']['intent'] == 'activate'
    assert request['sources'][0]['offset'] == 20 and request['sources'][0]['revision'] == 2
    assert 'role' not in request['sources'][0] and 'occurred_at' not in request['sources'][0]


def test_personal_record_is_explicit_and_title_reason_remain_optional():
    request = proposal_request(flat(scope='personal', title='解释器', reason='明确选择'), [source()])
    assert request['scope'] == 'personal'
    assert request['proposal']['title'] == '解释器' and request['proposal']['reason'] == '明确选择'


def test_update_uses_only_the_sent_snapshot_and_preserves_omitted_metadata():
    snapshot = old()
    request = proposal_request(flat(memory_id=snapshot['memory_id']), [source()], [snapshot])
    assert request['action'] == 'update' and request['scope'] == 'personal'
    assert request['expected_version'] == 4
    p = request['proposal']
    assert p['title'] == '既有标题' and p['reason'] == '旧建议'
    assert p['aliases'] == ['venv'] and p['tags'] == ['python'] and p['gaps'] == ['待用户确认']
    assert (p['machine'], p['module'], p['target']) == ('Mac', 'tests', '.venv')
    assert p['basis'] == 'user_declaration' and p['intent'] == 'activate'
    assert snapshot == old()  # Mapping must not mutate the evidence snapshot.


def test_update_explicit_empty_lists_close_gaps_but_null_keeps_old_values():
    request = proposal_request(flat(memory_id='a' * 32, title=None, scope=None,
        aliases=[], gaps=[], machine=None, target='new-venv'), [source()], [old()])
    p = request['proposal']
    assert p['gaps'] == p['aliases'] == [] and p['tags'] == ['python']
    assert p['title'] == '既有标题' and p['machine'] == 'Mac' and p['target'] == 'new-venv'


@pytest.mark.parametrize('changes', [dict(action='record'), dict(expected_version=99),
    dict(proposal={}), dict(tags=[]), dict(deadline=1), dict(occurred_at=1),
    dict(body='x' * 801), dict(aliases=['a'] * 6), dict(scope='both'), dict(intent=None)])
def test_internal_control_fields_and_invalid_meaning_are_not_repaired(changes):
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        proposal_request(flat(**changes), [source()])


@pytest.mark.parametrize('rows,changes', [([], {}), ([old(complete=False)], {}),
    ([old(), old(scope='workspace')], {}), ([old()], {'scope': 'workspace'}),
    ([old()], {'kind': 'rule'})])
def test_update_cannot_select_hidden_ambiguous_incomplete_or_different_scope_records(rows, changes):
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        proposal_request(flat(memory_id='a' * 32, **changes), [source()], rows)


def test_prepare_sends_compact_sources_and_keeps_the_trusted_envelope(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import _prepare
    async def scenario():
        at = time.time() - 10
        save(archive, [HumanMessage(id='human', content='使用.venv',
            additional_kwargs={'naiyue_human_input': True})], occurred_at=at)
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[]))
        try:
            prepared = await _prepare(service, job, job.upper)
            trusted, messages = prepared[:2]
            payload = json.loads(messages[-1].content)
            visible = payload['batch']['sources'][0]
            assert set(visible) == {'source_key', 'role', 'text', 'occurred_at', 'name',
                'tool_status', 'partial'}
            assert visible['source_key'] == trusted['sources'][0]['source_key']
            assert trusted['sources'][0]['message_id'] == 'human'
            assert set(payload['batch']) == {'sources', 'complete', 'skipped'}
            assert not job.model.seen and visible['partial'] is False
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_frozen_update_cannot_overwrite_a_newer_version_in_real_store(archive, tmp_path):
    from naiyue_test_agent.tools.memory import MemoryInput
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv',
            additional_kwargs={'naiyue_human_input': True})], occurred_at=time.time() - 10)
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[]))
        try:
            sources = batch(archive)['sources']
            created = service.memory.query('session', service.storage.sessions.get('session')['project_id'], proposal_request(flat(intent='candidate'), sources))['item']
            saved = service.memory.query('session', service.storage.sessions.get('session')['project_id'], MemoryInput(action='read', scope='workspace',
                status='candidate', memory_id=created['memory_id']).model_dump(mode='json'))['items'][0]
            save(archive, [HumanMessage(id='confirmation', content='以后使用.venv',
                additional_kwargs={'naiyue_human_input': True})], occurred_at=time.time() - 1)
            sources = batch(archive)['sources']
            frozen = proposal_request(flat(memory_id=created['memory_id'], gaps=[],
                sources=[dict(source_key='2:0', quote='使用.venv')]), sources, [saved])
            assert frozen['expected_version'] == 1
            service.memory.query('session', service.storage.sessions.get('session')['project_id'], frozen)
            with pytest.raises(MemoryReadError, match='memory_conflict'):
                service.memory.query('session', service.storage.sessions.get('session')['project_id'], frozen)
            row = archive.database.connection.execute('SELECT version,status FROM naiyue_memories').fetchone()
            assert tuple(row) == (2, 'active')
        finally:
            service.memory.close()
    asyncio.run(scenario())


def test_flat_worker_saves_valid_items_independently_of_bad_old_envelopes(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def scenario():
        save(archive, [HumanMessage(id='human', content='使用.venv',
            additional_kwargs={'naiyue_human_input': True})])
        service, job, events = fixture(archive, tmp_path,
            StreamingToolModel(replies=[response([flat(), {'action': 'invented'}])]))
        try:
            await drive(service, job)
            assert job.saved == job.overview_saved == job.failed == 1
            assert job.result_status == 'partial' and len(job.model.seen) == 1
            assert len([e for e in events if e.event_type == 'model_call_started']) == 1
            assert len([e for e in events if e.event_type == 'model_call_finished']) == 1
        finally:
            service.memory.close()
    asyncio.run(scenario())
