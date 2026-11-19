"""Current extraction and organization prompts preserve evidence boundaries."""
import asyncio
import json

from langchain_core.messages import AIMessage

from memory_wiki_helpers import proposed
from naiyue_test_agent.application.memory.memory_job_runner import drive
from test_memory_batches import batch, result
from test_memory_semantic_inputs import inputs, prepared
from test_memory_wiki_worker import finish, read


def test_actual_extraction_request_preserves_concrete_topic_guidance(inputs):
    _, messages, _ = prepared(inputs)
    prompt = messages[0].content
    assert 'Keep concrete names from the cited evidence in matter' in prompt
    assert 'working_agreements' in prompt and 'stable_knowledge' in prompt


def test_actual_extraction_request_allows_partial_confirmation_without_invention(inputs):
    _, messages, _ = prepared(inputs)
    prompt = messages[0].content
    assert 'Partial confirmation may be proposed without the old agreement in this batch' in prompt
    assert 'never invent the missing agreement' in prompt


def test_named_topic_and_partial_confirmation_reach_wiki_worker(inputs):
    from scripts.memory_evaluation_cases import seed_sources
    from test_async_application import StreamingToolModel

    async def run():
        service, job, old, _, at = inputs
        text = '本地回归仍沿用原约定且CI不在本次确认范围内。'
        saved = seed_sources(service.storage, 'probe', [dict(id='confirm-local', role='user', text=text)], at=at+30)
        job.upper = saved['upper']
        job.released = True
        eid = next(unit['evidence_id'] for unit in batch(service.storage, session_id='probe',
            thread_id='probe')['units'] if unit['message_id']=='confirm-local')
        proposal = proposed(matter='本地回归环境', subject='本地回归环境',
            body='本地仍沿用原约定，CI不在此次确认范围。', sources=[dict(evidence_id=eid)])
        job.model = StreamingToolModel(replies=[
            AIMessage(content=json.dumps(result(proposals=[proposal]))),
            read(dict(action='list_pages', topic='working_agreements')),
            read(dict(action='read_page', page_id=old['page_id'])),
            finish(decisions=[dict(relation='supplement', proposal_ids=['P1'], old_refs=['O1'],
                target_old_ref='O1', confirmation_scope='partial', evidence_ids=[eid])]),
        ])
        await drive(service, job)
        assert job.saved == job.overview_saved == 1 and job.failed == 0
        assert job.result_status == 'completed' and len(job.model.seen) == 4
        material = json.loads(job.model.seen[1][-1].content)
        assert material['proposals'][0]['matter'] == '本地回归环境'
        connection = service.storage.database.connection
        row = connection.execute('SELECT * FROM naiyue_memories WHERE memory_id=?', (old['memory_id'],)).fetchone()
        assert row['version'] == 3 and row['expires_at'] == old['expires_at']
        assert row['body'] == inputs[3][4]['sources'][0]['text']
        sources = service.storage.memory.wiki_sources([(old['memory_id'], 3)])
        assert {(s['message_id'], s['quote']) for s in sources} == {
            ('probe-e11-prior-assistant', row['body']), ('confirm-local', text)}

    asyncio.run(run())


def test_organization_prompt_treats_unconfirmed_ci_as_supplement():
    from naiyue_test_agent.application.memory.memory_wiki_protocol import ORGANIZE_PROMPT

    assert 'CI is not yet confirmed' in ORGANIZE_PROMPT
    assert 'choose supplement' in ORGANIZE_PROMPT
    assert 'not a correction of the existing CI rule' in ORGANIZE_PROMPT


def test_extraction_scope_guidance_keeps_project_exceptions_local(inputs):
    _, messages, _ = prepared(inputs)
    prompt = messages[0].content
    assert 'From now on does not establish cross-project scope' in prompt
    assert 'keep the project exception out of the personal proposal' in prompt
