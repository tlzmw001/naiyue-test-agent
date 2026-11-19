"""Two-stage contracts never turn a bad group into an independent write."""
import json
import pytest
from test_memory_batches import archive
from test_memory_readonly import memory
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.memory_settings import MemorySettings
from test_memory_batches import evidence_batch,proposed


def prepared():
    from naiyue_test_agent.application.memory.memory_proposals import bind_extraction
    batch=evidence_batch(dict(session_id='s',thread_id='t',message_id='m',revision=1,
        conversation_revision=0,text='以后使用.venv',occurred_at=2000.,user_origin=True))
    return bind_extraction(dict(proposed(),matter='本地回归环境'),batch,1)


def decision(**changes):
    return dict(proposal_ids=['P1'],relation='new',target_old_ref=None,support='direct',
        confirmation_scope='whole',claim=dict(kind='context',subject='pytest环境',body='使用.venv',
            basis='user_declaration',intent='activate'),new_evidence_ids=['E1'],old_evidence_refs=[],
        reason='明确选择',gaps=[])|changes


def group(**changes):
    return dict(group_id='G1',proposal_ids=['P1'],old_refs=[])|changes


def test_extraction_rejects_old_target_and_forged_identity():
    from naiyue_test_agent.application.memory.memory_proposals import bind_extraction
    bound=prepared()
    assert bound['proposal_id']=='P1'
    for change in (dict(memory_id='a'*32),dict(identity_terms=['TC-99'])):
        with pytest.raises(MemoryReadError):
            bind_extraction(dict(proposed(),matter='本地环境',**change),
                evidence_batch(dict(session_id='s',thread_id='t',message_id='m',revision=1,
                    conversation_revision=0,text='使用.venv')),1)


def test_group_compiles_program_owned_sources_only():
    from naiyue_test_agent.application.memory.memory_proposals import compile_comparison_decisions
    rows=compile_comparison_decisions(dict(group_id='G1',decisions=[decision()]),group(),{'P1':prepared()}, {})
    request,confirmation=rows[0]
    assert request['action']=='record' and confirmation=='whole'
    assert request['sources'][0]['message_id']=='m'


@pytest.mark.parametrize('changes',[dict(proposal_ids=['P2']),dict(new_evidence_ids=['E99']),
    dict(target_old_ref='O1'),dict(old_evidence_refs=['O1:S1'])])
def test_bad_group_has_no_compiled_writes(changes):
    from naiyue_test_agent.application.memory.memory_proposals import compile_comparison_decisions
    with pytest.raises(MemoryReadError):
        compile_comparison_decisions(dict(group_id='G1',decisions=[decision(**changes)]),group(),{'P1':prepared()}, {})


def test_organization_routes_proposal_by_scope_and_topic_even_without_new_rule_text():
    from naiyue_test_agent.application.memory.memory_wiki_protocol import bind_extraction
    from memory_wiki_helpers import proposed as wiki_proposal

    batch = evidence_batch(dict(session_id='s', thread_id='t', message_id='m', revision=1,
        conversation_revision=0, text='继续沿用约定', occurred_at=2000., user_origin=True))
    p = bind_extraction(wiki_proposal(matter='本地回归环境', body='确认继续沿用'), batch, 1)
    assert (p['scope'], p['topic'], p['matter']) == ('workspace', 'working_agreements', '本地回归环境')
    assert p['sources'][0]['quote'] == '继续沿用约定'


def test_shared_old_target_forms_one_complete_group():
    from naiyue_test_agent.application.memory.memory_comparison import connected_groups
    groups=connected_groups([dict(proposal_id='P1',old_refs=['O1']),
        dict(proposal_id='P2',old_refs=['O1','O2']),dict(proposal_id='P3',old_refs=[])])
    assert [g['proposal_ids'] for g in groups]==[['P1','P2'],['P3']]


def _wiki_worker_case(archive, tmp_path, replies):
    from langchain_core.messages import HumanMessage
    from test_async_application import StreamingToolModel
    from test_memory_batches import save
    from test_memory_job_runner import fixture
    import time

    now = time.time()
    archive.memory.prepare_wiki(now=now)
    save(archive, [HumanMessage(id='u', content='以后使用.venv',
        additional_kwargs={'naiyue_human_input': True})], occurred_at=now - 1)
    return fixture(archive, tmp_path, StreamingToolModel(replies=replies))


def _wiki_extract(**changes):
    from langchain_core.messages import AIMessage
    from memory_wiki_helpers import proposed as wiki_proposal
    from test_memory_batches import result

    return AIMessage(content=json.dumps(result(proposals=[wiki_proposal(matter='本地回归环境')]) | changes))


def _wiki_add():
    from test_memory_wiki_worker import finish, read

    return [
        read(dict(action='list_pages', topic='working_agreements')),
        finish(decisions=[dict(relation='add', proposal_ids=['P1'], destination='N1')],
               page_changes=[dict(ref='N1', topic='working_agreements', title='本地回归环境', description='')]),
    ]


def test_worker_uses_one_extraction_and_bounded_organization(archive, tmp_path):
    import asyncio
    from naiyue_test_agent.application.memory.memory_job_runner import drive

    async def run():
        service, job, events = _wiki_worker_case(archive, tmp_path, [_wiki_extract(), *_wiki_add()])
        try:
            await drive(service, job)
            assert job.saved == job.overview_saved == 1 and job.failed == 0
            assert len(job.model.seen) == 3 and not job.model.replies
            assert 'related_memories' not in job.model.seen[0][-1].content
            assert json.loads(job.model.seen[1][-1].content)['proposals'][0]['evidence_ids'] == ['E1']
            starts = [e.data.invocation_id for e in events if e.event_type == 'model_call_started']
            finishes = [e.data.invocation_id for e in events if e.event_type == 'model_call_finished']
            assert len(starts) == 3 and starts == finishes
        finally:
            service.memory.close()
    asyncio.run(run())


def test_bad_comparison_keeps_overview_without_candidate_fallback(archive, tmp_path):
    import asyncio
    from langchain_core.messages import AIMessage
    from naiyue_test_agent.application.memory.memory_job_runner import drive

    async def run():
        service, job, _ = _wiki_worker_case(archive, tmp_path, [_wiki_extract(), AIMessage(content='{broken')])
        try:
            await drive(service, job)
            assert job.overview_saved == 1 and job.saved == 0 and job.failed == 1
            assert len(job.model.seen) == 2 and not job.model.replies and job.start == (2, 0)
            assert archive.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0] == 0
        finally:
            service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize('old_source_missing', [False, True])
def test_worker_corrects_same_id_even_when_old_quote_unavailable(archive, tmp_path, old_source_missing):
    import asyncio
    import time
    from langchain_core.messages import HumanMessage, AIMessage
    from memory_wiki_helpers import proposed as wiki_proposal
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from naiyue_test_agent.application.memory.memory_wiki_protocol import bind_extraction
    from test_async_application import StreamingToolModel
    from test_memory_batches import save, result, batch as make_batch
    from test_memory_job_runner import fixture
    from test_memory_wiki_pagination import decode_content
    from test_memory_wiki_worker import finish, read

    async def run():
        now = time.time()
        archive.memory.prepare_wiki(now=now)
        old_text = '本地回归使用系统Python'
        text = '以后本地回归使用.venv，CI保持原样。'
        save(archive, [HumanMessage(id='old', content=old_text,
            additional_kwargs={'naiyue_human_input': True})], occurred_at=now-20)
        model = StreamingToolModel(replies=[])
        service, job, _ = fixture(archive, tmp_path, model)
        try:
            raw = wiki_proposal(matter='本地回归环境', subject='本地回归环境', body=old_text)
            old = bind_extraction(raw, make_batch(archive), 1)
            request = dict(action='record', scope='workspace', proposal=old['proposal'], sources=old['sources'],
                destination={'new_page': dict(topic=raw['topic'], title=raw['title'], description='')})
            first = service.memory.query_wiki('session', job.project_id, request, now=now-19)['item']
            save(archive, [HumanMessage(id='new', content=text,
                additional_kwargs={'naiyue_human_input': True})], occurred_at=now-10)
            if old_source_missing:
                with archive.database.transaction(write=True) as c:
                    c.execute('DELETE FROM naiyue_chat_messages WHERE message_id=?', ('old',))
            built = make_batch(archive)
            old_result = service.memory.query_wiki('session', job.project_id, dict(action='read',
                scope='workspace', memory_id=first['memory_id']), now=now)
            proof, = decode_content(old_result)['entries'][0]['sources']
            assert proof['available'] is not old_source_missing
            if old_source_missing:
                assert 'quote' not in proof
            eid = next(u['evidence_id'] for u in built['units'] if u['message_id'] == 'new')
            p = dict(raw, body=text, sources=[dict(evidence_id=eid)])
            new = bind_extraction(p, built, 1)
            d = dict(relation='correction', proposal_ids=['P1'], old_refs=['O1'], target_old_ref='O1',
                     confirmation_scope='whole', evidence_ids=[eid], proposal=new['proposal'])
            model.replies = [AIMessage(content=json.dumps(result(proposals=[p]))),
                read(dict(action='list_pages', topic=raw['topic'])),
                read(dict(action='read_page', page_id=first['page_id'])), finish(decisions=[d])]
            job.upper = 2
            await drive(service, job)
            assert job.saved == 1 and job.failed == 0 and len(model.seen) == 4
            row = archive.database.connection.execute('SELECT memory_id,version,body FROM naiyue_memories').fetchone()
            assert tuple(row) == (first['memory_id'], 2, text)
            changes = archive.database.connection.total_changes
            page = service.memory.query_wiki('session', job.project_id, dict(action='read_page',
                scope='workspace', page_id=first['page_id']), now=now)
            recalled, = decode_content(page)['entries']
            assert (recalled['memory_id'], recalled['version'], recalled['body']) == tuple(row)
            assert archive.database.connection.total_changes == changes
        finally:
            service.memory.close()
    asyncio.run(run())


def test_duplicate_target_invalidates_all_writes_in_group():
    from naiyue_test_agent.application.memory.memory_proposals import compile_comparison_decisions
    one=prepared();two=dict(one,proposal_id='P2')
    old=dict(memory_id='a'*32,version=1,kind='context',scope='workspace',sources=[],body='使用.venv',
        subject='pytest环境',machine=None,module=None,target=None,title='环境',reason='选择',tags=[],aliases=[],gaps=[],deadline_at=None)
    d=decision(relation='supplement',target_old_ref='O1')
    with pytest.raises(MemoryReadError,match='memory_comparison_output_invalid'):
        compile_comparison_decisions(dict(group_id='G1',decisions=[d,dict(d,proposal_ids=['P2'])]),
            group(proposal_ids=['P1','P2'],old_refs=['O1']),{'P1':one,'P2':two},{'O1':old})


def test_update_only_permission_is_not_preemptively_treated_as_record(memory):
    import time
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    from naiyue_test_agent.tools.memory_wiki_actions import WikiProposal
    from test_memory_lifecycle import source
    from test_memory_wiki_mutations import args
    from test_memory_wiki_organization import prepared

    storage, access, project, items, ledger = prepared(memory, 1)
    ref = source(access, 'caller', text='此前局部约定仍成立', at=time.time()-1)
    value = WikiProposal.model_validate(args(sources=[])['proposal']).model_dump()
    bound = dict(proposal=value, evidence_ids=['E1'], sources=[ref],
                 scope_evidence_ids=[], evidence=[dict(evidence_id='E1', origin='new')])
    result = dict(step='finish', decisions=[dict(relation='supplement', proposal_ids=['P1'],
        old_refs=['O1'], target_old_ref='O1', confirmation_scope='partial', evidence_ids=['E1'])])
    actions = []

    def allowed(request):
        actions.append(request['action'])
        return request['action'] == 'update'

    assert commit_group(access, 'caller', project, [(ledger, {'P1': bound}, result)], allowed) == 1
    assert actions and set(actions) == {'update'}
    row = storage.database.connection.execute('SELECT version,expires_at FROM naiyue_memories').fetchone()
    assert tuple(row) == (2, items[0]['expires_at'])


def _two_wiki_proposals():
    from memory_wiki_helpers import proposed as wiki_proposal

    return [
        wiki_proposal(matter='环境'),
        wiki_proposal(matter='报告语言', topic='stable_knowledge', subject='报告语言',
                      body='报告使用中文'),
    ]


def _second_topic_add():
    from test_memory_wiki_worker import finish, read

    return [read(dict(action='list_pages', topic='stable_knowledge')),
        finish(decisions=[dict(relation='add', proposal_ids=['P2'], destination='N2')],
            page_changes=[dict(ref='N2', topic='stable_knowledge', title='报告语言', description='')])]


def test_comparison_bad_unroutable_group_does_not_erase_independent_group(archive, tmp_path):
    import asyncio
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from test_memory_wiki_worker import finish

    async def run():
        replies = [_wiki_extract(proposals=_two_wiki_proposals()),
            finish(decisions=[dict(relation='supplement', proposal_ids=['P1'], old_refs=['O99'],
                                  target_old_ref='O99', evidence_ids=['E1'])]), *_second_topic_add()]
        service, job, _ = _wiki_worker_case(archive, tmp_path, replies)
        try:
            await drive(service, job)
            assert job.saved == 1 and job.failed == 1 and job.overview_saved == 1
            assert len(job.model.seen) == 4 and not job.model.replies
            assert 'wiki_commit_failed' in job.skipped
            assert [r[0] for r in archive.database.connection.execute(
                'SELECT body FROM naiyue_memories')] == ['报告使用中文']
        finally:
            service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize('constraint', ['bytes', 'tokens'])
def test_oversized_group_is_skipped_without_truncating_independent_group(archive, tmp_path, monkeypatch, constraint):
    import asyncio
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    from test_memory_wiki_worker import read

    async def run():
        replies = [_wiki_extract(proposals=_two_wiki_proposals()),
            read(dict(action='list_pages', topic='working_agreements')), *_second_topic_add()]
        service, job, _ = _wiki_worker_case(archive, tmp_path, replies)
        service.settings = service.memory.settings = MemorySettings(
            comparison_material_bytes=4096 if constraint == 'bytes' else 150000, max_input_tokens=16000)
        original = Ledger.read

        def oversized(ledger, *args, **kwargs):
            result = original(ledger, *args, **kwargs)
            if ledger.topic == 'working_agreements':
                return dict(result, content=('合成旧页。' * 500 if constraint == 'bytes' else 'synthetic older page body ' * 3500))
            return result

        monkeypatch.setattr(Ledger, 'read', oversized)
        try:
            await drive(service, job)
            assert job.saved == 1 and job.overview_saved == 1 and job.failed == 1
            assert len(job.model.seen) == 4 and not job.model.replies
            assert job.skipped == ['wiki_organization_failed']
            assert [r[0] for r in archive.database.connection.execute(
                'SELECT body FROM naiyue_memories')] == ['报告使用中文']
        finally:
            service.memory.close()
    asyncio.run(run())


def test_failed_lookup_discovered_target_blocks_connected_proposal():
    from naiyue_test_agent.application.memory.memory_organization_groups import connected_groups
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger

    shared_page = 'a' * 32
    failed = Ledger('workspace', 'working_agreements', writable=[shared_page])
    connected = Ledger('workspace', 'stable_knowledge', writable=[shared_page])
    independent = Ledger('workspace', 'collaboration')
    target = dict(step='finish', decisions=[
        dict(relation='add', proposal_ids=['P2'], destination=shared_page)])
    unrelated = dict(step='finish', decisions=[
        dict(relation='add', proposal_ids=['P3'], destination='N1')])
    groups = connected_groups([
        dict(ledger=failed, proposals={'P1': {}}, finished=None),
        dict(ledger=connected, proposals={'P2': {}}, finished=target),
        dict(ledger=independent, proposals={'P3': {}}, finished=unrelated),
    ])
    assert [[p for unit in g for p in unit['proposals']] for g in groups] == [['P1','P2'], ['P3']]
    assert [any(unit['finished'] is None for unit in g) for g in groups] == [True, False]


def test_invalid_overview_does_not_discard_valid_proposal(archive, tmp_path):
    import asyncio
    from naiyue_test_agent.application.memory.memory_job_runner import drive

    async def run():
        replies = [_wiki_extract(overview={'wrong': 'PRIVATE'}), *_wiki_add()]
        service, job, _ = _wiki_worker_case(archive, tmp_path, replies)
        try:
            await drive(service, job)
            assert job.saved == 1 and job.overview_saved == 0 and job.failed == 1
            assert len(job.model.seen) == 3 and not job.model.replies
            assert archive.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0] == 1
        finally:
            service.memory.close()
    asyncio.run(run())


def test_failed_extraction_batch_advances_without_retrying_known_range(archive,tmp_path):
    import asyncio
    from langchain_core.messages import AIMessage,HumanMessage
    from test_memory_batches import save,result
    from test_memory_job_runner import fixture
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def run():
        save(archive,[HumanMessage(id='one',content='第一条完整消息。'),HumanMessage(id='two',content='第二条完整消息。')])
        service,job,_=fixture(archive,tmp_path,StreamingToolModel(replies=[AIMessage(content='{broken'),AIMessage(content=json.dumps(result()))]),batch_max_messages=1)
        job.upper=2
        try:
            await drive(service,job)
            assert job.batches==2 and len(job.model.seen)==2 and job.failed==1 and job.overview_saved==1
            assert job.start==(3,0) and any(s.startswith('failed_batch:') for s in job.skipped)
            second=json.loads(job.model.seen[1][-1].content)['batch']['units']
            assert [u['text'] for u in second if u['origin']=='new']==['第二条完整消息。']
        finally:service.memory.close()
    asyncio.run(run())
