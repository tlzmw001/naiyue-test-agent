"""Wiki extraction binds symbolic evidence without retired kind or copied scope quotes."""
import json

import pytest
from langchain_core.messages import AIMessage

from naiyue_test_agent.application.memory.memory_wiki_protocol import (
    OrganizationResult,
    WikiExtractionResult,
    bind_extraction,
    parse,
)
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_batches import archive


def test_wiki_extraction_contract_has_no_kind():
    from naiyue_test_agent.application.memory.memory_wiki_protocol import WikiExtractionResult,bind_extraction
    schema=WikiExtractionResult.model_json_schema()
    assert 'kind' not in str(schema)
    with pytest.raises(MemoryReadError):bind_extraction({},dict(evidence_map={}),1)


def test_organization_contract_has_read_and_finish():
    assert OrganizationResult.model_validate(dict(result=dict(step='finish',decisions=[],page_changes=[],links=[])))


@pytest.mark.parametrize(('schema', 'payload'), [
    (WikiExtractionResult, {'overview': {'goal': '', 'progress': '', 'blockers': '', 'next_step': ''}, 'proposals': []}),
    (OrganizationResult, {'result': {'step': 'read', 'requests': [{'action': 'list_pages', 'topic': 'working_agreements'}]}}),
    (OrganizationResult, {'result': {'step': 'finish', 'decisions': []}}),
])
@pytest.mark.parametrize(('language', 'newline'), [('json', '\n'), ('JSON', '\r\n')])
def test_wiki_text_decodes_one_complete_json_fence(schema, payload, language, newline):
    body = json.dumps(payload)
    response = AIMessage(content=f'  ```{language}{newline}{body}{newline}```  ',
                         response_metadata={'finish_reason': 'stop'})
    assert parse(response, 'text', schema) == parse(body, 'text', schema) == payload
    schema.model_validate(parse(response, 'text', schema))


@pytest.mark.parametrize('body', [
    'Here is the result:\n```json\n{}\n```',
    '```json\n{}\n```\nDone.',
    '```json\n{}\n```\n```json\n{}\n```',
    '```json\n{}',
    '```python\n{}\n```',
    '```json\n{"key": 1, "key": 2}\n```',
    '```json\n{"key": NaN}\n```',
])
def test_wiki_text_keeps_rejecting_other_invalid_json_output(body):
    with pytest.raises(MemoryReadError, match='memory_result_invalid'):
        parse(body, 'text', WikiExtractionResult)


def test_wiki_fence_compatibility_is_text_only_and_respects_truncation():
    fenced = '```json\n{"result":{"step":"finish","decisions":[]}}\n```'
    with pytest.raises(MemoryReadError, match='memory_result_invalid'):
        parse(AIMessage(content=fenced), 'json_schema', OrganizationResult)
    with pytest.raises(MemoryReadError, match='memory_result_truncated'):
        parse(AIMessage(content=fenced, response_metadata={'finish_reason': 'length'}), 'text', OrganizationResult)

@pytest.mark.parametrize('scope', [{'scope': 'workspace'}, {}])
@pytest.mark.parametrize('unused', [['E1'], ['E404', 'E404'], None, {'unexpected': True}])
def test_workspace_ignores_scope_proof_but_binds_original_sources(scope, unused):
    from memory_wiki_helpers import proposed
    from test_memory_batches import evidence_batch
    from test_memory_flat_contract import source

    raw = proposed(scope_evidence_ids=unused)
    del raw['scope']
    batch = evidence_batch(source(text='项目使用隔离环境', user_origin=True))
    bound = bind_extraction(raw | scope, batch, 1)
    assert bound['scope'] == 'workspace' and bound['scope_evidence_ids'] == []
    assert bound['evidence_ids'] == ['E1'] and bound['sources'][0]['quote'] == '项目使用隔离环境'
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        bind_extraction(raw | scope | {'sources': [{'evidence_id': 'E404'}]}, batch, 1)


@pytest.mark.parametrize(('ids', 'user_origin', 'accepted'), [
    (['E1'], True, True), ([], True, False), (['E1'], False, False),
    (['E404'], True, False), (['E1', 'E1'], True, False), (None, True, False),
])
def test_personal_still_requires_bound_user_scope_proof(ids, user_origin, accepted):
    from memory_wiki_helpers import proposed
    from test_memory_batches import evidence_batch
    from test_memory_flat_contract import source

    raw = proposed(scope='personal', scope_evidence_ids=ids)
    batch = evidence_batch(source(user_origin=user_origin))
    if accepted:
        assert bind_extraction(raw, batch, 1)['scope_evidence_ids'] == ['E1']
    else:
        with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
            bind_extraction(raw, batch, 1)


@pytest.mark.parametrize(('changes', 'detail'), [
    ({'topic': 'PRIVATE_VALUE'}, 'fields_invalid'),
    ({'sources': [{'evidence_id': 'E1'}, {'evidence_id': 'E404'}]}, 'evidence_id_missing'),
    ({'scope': 'personal'}, 'scope_evidence_required'),
    ({'scope': 'personal', 'scope_evidence_ids': ['E404']}, 'scope_evidence_not_bound'),
])
def test_wiki_binding_failure_preserves_independent_memory_and_reports_safe_reason(
        archive, tmp_path, caplog, changes, detail):
    import asyncio
    from memory_wiki_helpers import proposed
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from test_memory_proposal_comparison import _wiki_add, _wiki_extract, _wiki_worker_case
    from test_memory_wiki_worker import finish

    async def run():
        directory, good = _wiki_add()
        value = json.loads(good.content)['result']
        value['decisions'][0]['proposal_ids'] = ['P2']
        replies = [_wiki_extract(proposals=[proposed(**changes), proposed()]), directory,
                   finish(decisions=value['decisions'], page_changes=value['page_changes'])]
        service, job, _ = _wiki_worker_case(archive, tmp_path, replies)
        try:
            await drive(service, job)
            assert (job.overview_saved, job.saved, job.failed) == (1, 1, 1)
            assert f'detail={detail}' in caplog.text
            assert 'PRIVATE_VALUE' not in caplog.text and 'E404' not in caplog.text
        finally:
            service.memory.close()
    asyncio.run(run())


def test_extraction_uses_configured_limits_and_preserves_overview_on_proposal_overflow(archive, tmp_path, caplog):
    import asyncio
    from memory_wiki_helpers import proposed
    from naiyue_test_agent.application.memory.memory_job_runner import drive
    from naiyue_test_agent.memory_settings import MemorySettings
    from test_memory_proposal_comparison import _wiki_extract, _wiki_worker_case

    async def run():
        service, job, _ = _wiki_worker_case(archive, tmp_path, [_wiki_extract(proposals=[proposed()])])
        service.settings = service.memory.settings = MemorySettings(max_proposals=0, overview_chars=600)
        try:
            await drive(service, job)
            prompt = job.model.seen[0][0].content
            assert '<=600 total chars; proposals <=0.' in prompt
            assert '"maxItems": 0' in prompt
            assert (job.overview_saved, job.saved, job.failed) == (1, 0, 1)
            assert job.skipped == ['memory_proposal_limit']
            stored = archive.database.connection.execute(
                'SELECT skipped_json FROM naiyue_memory_overviews WHERE session_id=?', (job.target_session_id,)
            ).fetchone()
            assert json.loads(stored[0]) == []
            assert archive.memory.overview(job.target_session_id)['coverage']['skipped'] == []
            assert 'reason=memory_proposal_limit' in caplog.text
            assert len(job.model.seen) == 1
        finally:
            service.memory.close()
    asyncio.run(run())
