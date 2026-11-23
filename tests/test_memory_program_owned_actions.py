"""Models choose evidence/actions; the compiler owns unchanged content."""
import copy
import json
import logging

import pytest
from naiyue_test_agent.application.memory.memory_proposals import (
    Claim, Extraction, ComparisonResult, compile_comparison_decisions)
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_proposal_comparison import prepared, decision, group
from test_memory_s3_review_fixes import old_row
from test_memory_lifecycle import memory, storage_memory, source
from test_memory_batches import archive


def compile_one(d, p=None, old=None):
    p=p or prepared()
    return compile_comparison_decisions(dict(group_id='G1',decisions=[d]),
        group(old_refs=['O1'] if old else []),{'P1':p},{'O1':old} if old else {})


@pytest.mark.parametrize('confirmation',['partial','unknown'])
@pytest.mark.parametrize('extra',[False,True])
def test_source_only_uses_old_content_not_model_copy(confirmation,extra,caplog):
    old=dict(old_row(),basis='user_declaration',status='candidate',gaps=['原有疑点'])
    extra_claim=dict(decision()['claim'],kind='convention',body='PRIVATE-IGNORED 改写',
        subject='改写事项',title='改写标题',machine='other',aliases=['other'],gaps=[])
    d=decision(relation='supplement',target_old_ref='O1',confirmation_scope=confirmation,
        claim=extra_claim if extra else None,gaps=['模型新增疑点'])
    with caplog.at_level(logging.INFO):
        request,scope=compile_one(d,old=old)[0]
    assert scope==confirmation and request['expected_version']==old['version']
    for k in ('kind','body','subject','title','machine','module','target','basis','aliases','gaps','tags'):
        assert request['proposal'][k]==old[k]
    assert request['proposal']['intent']=='candidate'
    assert request['sources'][0]['message_id']=='m'
    assert 'PRIVATE-IGNORED' not in caplog.text
    if extra:assert 'memory_comparison_content_ignored' in caplog.text


@pytest.mark.parametrize('relation',['new','different_conditions','unresolved_conflict'])
def test_single_proposal_can_be_accepted_without_copying_claim(relation):
    p=prepared();before=copy.deepcopy(p)
    request,_=compile_one(decision(relation=relation,claim=None),p=p)[0]
    for k in ('kind','body','subject','basis'):assert request['proposal'][k]==p['proposal'][k]
    assert request['proposal']['intent']==('candidate' if relation=='unresolved_conflict' else 'activate')
    assert p==before


@pytest.mark.parametrize('extra_kind',[None,'convention'])
def test_comparison_kind_is_inherited_not_reclassified(extra_kind):
    claim=dict(decision()['claim']);claim.pop('kind')
    if extra_kind:claim['kind']=extra_kind
    request,_=compile_one(decision(claim=claim))[0]
    assert request['proposal']['kind']=='context'
    assert 'kind' not in Claim.model_json_schema()['required']
    assert 'kind' in Extraction.model_json_schema()['required']


@pytest.mark.parametrize('relation',['duplicate','unsupported'])
def test_no_write_actions_ignore_redundant_content(relation,caplog):
    old=old_row() if relation=='duplicate' else None
    d=decision(relation=relation,target_old_ref='O1' if old else None)
    with caplog.at_level(logging.INFO):assert compile_one(d,old=old)==[]
    assert 'memory_comparison_content_ignored' in caplog.text


@pytest.mark.parametrize('relation',['supplement','duplicate','unsupported'])
def test_actions_without_new_content_do_not_require_matching_input_kinds(relation):
    one=prepared();two=copy.deepcopy(one);two['proposal']['kind']='convention'
    old=dict(old_row(),basis='user_declaration',status='active')
    d=decision(proposal_ids=['P1','P2'],relation=relation,claim=None,
        target_old_ref=None if relation=='unsupported' else 'O1',confirmation_scope='partial')
    writes=compile_comparison_decisions(dict(group_id='G1',decisions=[d]),
        group(proposal_ids=['P1','P2'],old_refs=['O1']),{'P1':one,'P2':two},{'O1':old})
    if relation=='supplement':assert writes[0][0]['proposal']['kind']==old['kind']
    else:assert writes==[]


def test_reuse_cannot_silently_expand_chosen_evidence_or_choose_one_proposal():
    p=prepared();p['evidence_ids'].append('E2')
    p['evidence'].append(dict(p['evidence'][0],evidence_id='E2'))
    p['sources'].append(dict(p['sources'][0]))
    with pytest.raises(MemoryReadError):compile_one(decision(claim=None),p=p)
    p=prepared()
    with pytest.raises(MemoryReadError):
        compile_comparison_decisions(dict(group_id='G1',decisions=[decision(proposal_ids=['P1','P2'],claim=None)]),
            group(proposal_ids=['P1','P2']),{'P1':p,'P2':dict(p,proposal_id='P2')},{})


@pytest.mark.parametrize('change',[
    dict(new_evidence_ids=['E99']),dict(target_old_ref='O99'),dict(support='unsupported'),
    dict(support='inference'),dict(relation='correction'),dict(new_evidence_ids=[]),
])
def test_source_only_does_not_rescue_invalid_actions(change):
    d=decision(relation='supplement',target_old_ref='O1',confirmation_scope='partial',claim=None)
    with pytest.raises(MemoryReadError):compile_one(d|change,old=dict(old_row(),basis='user_declaration',status='active'))


def test_null_correction_still_requires_whole_fresh_support():
    old=old_row();p=prepared();p['proposal']['kind']='convention'
    d=decision(relation='correction',target_old_ref='O1',claim=None)
    request,_=compile_one(d,p,old)[0]
    assert request['proposal']['kind']=='convention' and request['memory_id']==old['memory_id']
    for change in (dict(confirmation_scope='partial'),dict(support='inference')):
        with pytest.raises(MemoryReadError):compile_one(d|change,p,old)


@pytest.mark.parametrize('state',['active','candidate','expired'])
def test_source_only_real_commit_preserves_every_content_clock_and_cas(memory,state):
    from test_memory_wiki_mutations import args

    access, sid, _, _ = memory
    access.storage.memory.prepare_wiki(now=1000.)
    project = access.storage.sessions.get(sid)['project_id']
    first_source = source(access, sid, at=1000.)
    initial = args(sources=[first_source])
    if state == 'candidate':
        initial['proposal'].update(intent='candidate', gaps=['待确认'])
    old = access.query_wiki(sid, project, initial, now=1000.)['item']
    at = 4000000. if state == 'expired' else 2000.
    ref = source(access, sid, text='只确认本地部分，CI未重新确认。', at=at)
    req = dict(action='update', scope='workspace', memory_id=old['memory_id'],
               expected_version=old['version'], change={'mode': 'supplement'}, sources=[ref])
    connection = access.storage.database.connection
    query = 'SELECT * FROM naiyue_memories WHERE memory_id=?'
    before = dict(connection.execute(query, (old['memory_id'],)).fetchone())
    if state == 'expired':
        with pytest.raises(MemoryReadError, match='^memory_conflict$'):
            access.query_wiki(sid, project, req, now=at+1)
        assert dict(connection.execute(query, (old['memory_id'],)).fetchone()) == before
        assert connection.execute('SELECT COUNT(*) FROM naiyue_memory_versions').fetchone()[0] == 1
        return
    result = access.query_wiki(sid, project, req, now=at+1)
    after = dict(connection.execute(query, (old['memory_id'],)).fetchone())
    assert after == dict(before, version=2, updated_at=at+1)
    refs = access.storage.memory.wiki_sources([(old['memory_id'], 2)])
    assert {row['message_id'] for row in refs} == {first_source['message_id'], ref['message_id']}
    assert result['item']['status'] == old['status']
    with pytest.raises(MemoryReadError, match='memory_conflict'):
        access.query_wiki(sid, project, req, now=at+2)


def test_ignored_content_logging_failure_cannot_change_action(monkeypatch):
    import naiyue_test_agent.application.memory.memory_proposals as module
    def broken(*a,**kw):raise RuntimeError('sink unavailable')
    monkeypatch.setattr(module.logger,'info',broken)
    assert compile_one(decision(relation='unsupported'))==[]


def test_prompt_teaches_actions_and_inheritance():
    from naiyue_test_agent.application.memory.memory_comparison import comparison_messages
    system=comparison_messages({})[0].content
    assert 'claim=null' in system and 'source-only' in system
    assert 'Do not copy' in system and 'Only when input scope is personal' in system
    assert json.loads(system.split('Output JSON schema:\n')[1])==ComparisonResult.model_json_schema()


@pytest.mark.parametrize('extra',[False,True])
@pytest.mark.parametrize('allowed',[False,True])
def test_worker_source_only_handles_missing_old_archive_without_retry(archive,tmp_path,extra,allowed):
    import asyncio,time
    from langchain_core.messages import AIMessage,HumanMessage
    from test_memory_batches import save, batch, result
    from memory_wiki_helpers import proposed
    from test_memory_wiki_worker import finish, read
    from test_memory_job_runner import fixture
    from test_async_application import StreamingToolModel
    from naiyue_test_agent.application.memory.memory_wiki_protocol import bind_extraction
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def run():
        now=time.time();old_text='本地和CI回归使用隔离环境。'
        archive.memory.prepare_wiki(now=now)
        save(archive,[HumanMessage(id='old',content=old_text,additional_kwargs={'naiyue_human_input':True})],occurred_at=now-20)
        model=StreamingToolModel(replies=[]);service,job,events=fixture(archive,tmp_path,model)
        try:
            p=dict(proposed(),matter='回归环境',subject='回归环境',body=old_text)
            bound = bind_extraction(p, batch(archive), 1)
            first = service.memory.query_wiki('session', job.project_id, dict(
                action='record', scope='workspace', proposal=bound['proposal'], sources=bound['sources'],
                destination={'new_page': dict(topic=p['topic'], title=p['title'], description='')}), now=now-19)['item']
            save(archive,[HumanMessage(id='new',content='本地回归保持原约定；CI不在本次确认范围。',
                additional_kwargs={'naiyue_human_input':True})],occurred_at=now-10)
            with archive.database.connection:
                archive.database.connection.execute('DELETE FROM naiyue_chat_messages WHERE message_id=?',('old',))
            eid=next(u['evidence_id'] for u in batch(archive)['units'] if u['message_id']=='new')
            p.update(body='仅确认本地回归，CI未重新确认。',sources=[dict(evidence_id=eid)])
            d = dict(relation='supplement', proposal_ids=['P1'], old_refs=['O1'], target_old_ref='O1',
                     confirmation_scope='partial', evidence_ids=[eid])
            if extra:
                d['proposal'] = dict(bound['proposal'], body=old_text + '本次只确认本地。')
            model.replies = [
                AIMessage(content=json.dumps(result(proposals=[p]))),
                read(dict(action='list_pages')),
                read(dict(action='read_page', page_id=first['page_id'])),
                finish(decisions=[d]),
            ]
            job.upper = 2
            service.allowed=lambda request:allowed or request['action']!='update'
            await drive(service,job)
            assert len(model.seen)==4 and not model.replies and job.saved==int(allowed) and job.failed==int(not allowed)
            assert job.overview_saved==1 and job.result_status==('completed' if allowed else 'partial')
            row=dict(archive.database.connection.execute('SELECT * FROM naiyue_memories').fetchone())
            assert row['memory_id']==first['memory_id'] and row['version']==1+int(allowed)
            assert row['body'] == old_text and row['last_confirmed_at'] == first['evidence_at']
            for key in ('evidence_at', 'expires_at', 'status'):
                assert row[key] == first[key]
            refs=archive.database.connection.execute('SELECT message_id FROM naiyue_memory_sources WHERE memory_version=?',
                (row['version'],)).fetchall()
            assert {r[0] for r in refs}==({'old','new'} if allowed else {'old'})
            assert archive.database.connection.execute("SELECT count(*) FROM naiyue_chat_messages WHERE message_id='old'").fetchone()[0]==0
            starts=[e for e in events if e.event_type=='model_call_started']
            ends=[e for e in events if e.event_type=='model_call_finished']
            assert len(starts)==len(ends)==4
            assert [e.data.invocation_id for e in starts]==[e.data.invocation_id for e in ends]
        finally:service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize('provider',['openai','responses','anthropic','ollama'])
@pytest.mark.parametrize('mode',['json_schema','function_calling'])
def test_native_adapter_reuses_bound_proposal_without_rewriting(archive,tmp_path,provider,mode):
    import asyncio
    from langchain_core.messages import HumanMessage
    from http_observation_helpers import sdk_catalog
    from test_memory_structured_output import wire_response
    from memory_wiki_helpers import proposed
    from langchain_core.messages import AIMessage
    from test_memory_batches import save,result
    from test_memory_job_runner import fixture
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    async def run():
        import time
        from test_memory_wiki_worker import finish, read

        at = time.time() - 1
        archive.memory.prepare_wiki(now=at)
        save(archive,[HumanMessage(id='u',content='使用.venv',additional_kwargs={'naiyue_human_input':True})],
             occurred_at=at)
        sent=[]
        replies = [
            AIMessage(content=json.dumps(result(proposals=[proposed()]))),
            read(dict(action='list_pages')),
            finish(decisions=[dict(relation='add', proposal_ids=['P1'], destination='N1')],
                   page_changes=[dict(ref='N1', topic='working_agreements', title='环境', description='')]),
        ]
        def serve(request):
            sent.append(json.loads(request.content))
            value = json.loads(replies[len(sent)-1].content)
            return wire_response(request, provider, mode, value,
                name='WikiExtractionResult' if len(sent) == 1 else 'OrganizationResult')
        async with sdk_catalog(provider,serve=serve) as catalog:
            model=catalog.get('A');model.profile={**(model.profile or {}),'memory_output':mode}
            service,job,events=fixture(archive,tmp_path,model)
            try:
                await drive(service,job)
                assert job.saved==1 and job.failed==0 and job.result_status=='completed'
                assert len(sent)==3
                row=archive.database.connection.execute('SELECT body FROM naiyue_memories').fetchone()
                assert row['body'] == proposed()['body']
                proof = archive.database.connection.execute('SELECT message_id FROM naiyue_memory_sources').fetchone()
                assert proof['message_id'] == 'u'
                assert len([e for e in events if e.event_type=='model_call_finished'])==3
            finally:service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize('confirmation',['partial','unknown'])
@pytest.mark.parametrize('refs',[['O1'],['O99:S99'],['O1','O1']])
def test_source_only_ignores_unused_old_diagnostic_refs(confirmation,refs,caplog):
    old=dict(old_row(),basis='user_declaration',status='active')
    d=decision(relation='supplement',target_old_ref='O1',confirmation_scope=confirmation,
        claim=None,old_evidence_refs=refs)
    with caplog.at_level(logging.INFO):request,scope=compile_one(d,old=old)[0]
    assert request['memory_id']==old['memory_id'] and scope==confirmation
    assert request['proposal']['body']==old['body']
    assert 'field=old_evidence_refs' in caplog.text
    assert 'O99:S99' not in caplog.text


def test_source_only_old_reference_prompt_is_explicit():
    from naiyue_test_agent.application.memory.memory_comparison import comparison_messages
    assert 'For source-only, return old_evidence_refs=[]' in comparison_messages({})[0].content


@pytest.mark.parametrize('relation',['correction','supplement'])
def test_other_write_relations_still_reject_bad_old_references(relation):
    d=decision(relation=relation,target_old_ref='O1',confirmation_scope='whole',old_evidence_refs=['O1'])
    with pytest.raises(MemoryReadError) as caught:compile_one(d,old=old_row())
    assert str(caught.value.__cause__)=='old_source'


@pytest.mark.parametrize('refs',[[{}],['x'*129]])
def test_source_only_still_rejects_malformed_reference_schema(refs):
    d=decision(relation='supplement',target_old_ref='O1',confirmation_scope='partial',
        claim=None,old_evidence_refs=refs)
    with pytest.raises(MemoryReadError):compile_one(d,old=old_row())
