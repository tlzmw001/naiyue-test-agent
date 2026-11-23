"""One pre-write correction across the production Wiki read/finish loop."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage
from naiyue_test_agent.application.memory.memory_job_runner import drive
from test_memory_batches import archive
from test_memory_proposal_comparison import _wiki_add, _wiki_extract, _wiki_worker_case
from test_memory_wiki_worker import msg


@pytest.mark.parametrize(('changes', 'field'), [
    ({'destination': None}, 'destination'),
    ({'destination': 'N404'}, 'destination'),
    ({'proposal_ids': []}, 'proposal_ids'),
    ({'proposal_ids': ['P1','P1']}, 'proposal_ids'),
    ({'evidence_ids': ['E404']}, 'evidence_ids'),
    ({'relation': 'supplement'}, 'target_old_ref'),
    ({'relation': 'correction'}, 'proposal'),
])
def test_finish_parameter_correction_commits_once(archive, tmp_path, changes, field):
    async def run():
        directory, correct = _wiki_add()
        first = json.loads(correct.content)
        first['result']['decisions'][0].update(changes)
        service, job, _ = _wiki_worker_case(archive, tmp_path, [_wiki_extract(), directory, msg(first), correct])
        try:
            await drive(service, job)
            assert (job.result_status, job.overview_saved, job.saved, job.failed) == ('completed', 1, 1, 0)
            assert len(job.model.seen) == 4 and not job.model.replies
            feedback = json.loads(job.model.seen[-1][-1].content)['parameter_feedback']
            assert field in str(feedback)
            assert archive.database.connection.execute('SELECT count(*) FROM naiyue_memories').fetchone()[0] == 1
        finally:
            service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize('invalid', [
    AIMessage(content='{broken PRIVATE_VALUE'),
    msg({'result':{'step':'read','requests':[{'action':'read_page'}]}}),
    msg({'result':{'step':'UNKNOWN_PRIVATE_VALUE'}}),
])
def test_bad_syntax_or_step_can_be_resubmitted_before_reads(archive, tmp_path, invalid):
    async def run():
        service, job, _ = _wiki_worker_case(archive, tmp_path, [_wiki_extract(), invalid, *_wiki_add()])
        try:
            await drive(service, job)
            assert (job.saved, job.failed, job.overview_saved) == (1, 0, 1)
            feedback = job.model.seen[2][-1].content
            assert 'parameter_feedback' in feedback and 'PRIVATE_VALUE' not in feedback
        finally:
            service.memory.close()
    asyncio.run(run())


def test_read_and_finish_share_correction_allowance(archive, tmp_path):
    async def run():
        directory, correct = _wiki_add()
        bad = json.loads(correct.content)
        del bad['result']['decisions'][0]['destination']
        service, job, _ = _wiki_worker_case(archive, tmp_path, [_wiki_extract(), msg({}), directory, msg(bad)])
        try:
            await drive(service, job)
            assert len(job.model.seen) == 4 and not job.model.replies
            assert job.failed == 1 and job.saved == 0 and job.overview_saved == 1
        finally:
            service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize('invalid', [
    AIMessage(content='{}', response_metadata={'finish_reason':'length'}),
    AIMessage(content='{}', additional_kwargs={'refusal':'declined'}),
    AIMessage(content='{"result": {}, "result": {}}'),
])
def test_refused_truncated_or_ambiguous_output_is_terminal(archive, tmp_path, invalid):
    async def run():
        service, job, _ = _wiki_worker_case(archive, tmp_path, [_wiki_extract(),invalid])
        try:
            await drive(service, job)
            assert len(job.model.seen) == 2 and job.saved == 0 and job.failed == job.overview_saved == 1
        finally:
            service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize(('decision', 'expected'), [
    ({'relation': 'move', 'proposal_ids': ['P1'], 'old_refs': ['O1'], 'target_old_ref': 'O1', 'destination': 'page'}, 'proposal_ids'),
    ({'relation': 'supplement', 'old_refs': ['O1','O1'], 'target_old_ref': 'O1'}, 'old_refs'),
    ({'relation': 'merge', 'old_refs': ['O1'], 'target_old_ref': 'O1'}, 'participants'),
    ({'relation': 'add', 'old_refs': ['O1'], 'destination': 'page'}, 'old_refs'),
])
def test_relation_specific_parameter_combinations(decision, expected):
    from naiyue_test_agent.application.memory.memory_organization_feedback import finish_feedback
    from naiyue_test_agent.application.memory.memory_wiki_protocol import Finish
    selected = {'P1': {'evidence_ids':['E1']}} if decision.get('proposal_ids') else {}
    plan = Finish.model_validate(dict(step='finish', decisions=[decision]))
    assert expected in str(finish_feedback(plan, selected))


def test_new_page_refs_in_links_are_declared():
    from naiyue_test_agent.application.memory.memory_organization_feedback import finish_feedback
    from naiyue_test_agent.application.memory.memory_wiki_protocol import Finish
    plan = Finish.model_validate(dict(step='finish', decisions=[], links=[{'page':'page','targets':['N4']}]))
    assert 'links[0].targets' in str(finish_feedback(plan, {}))


def test_merge_requires_cited_evidence_for_each_new_proposal():
    from naiyue_test_agent.application.memory.memory_organization_feedback import finish_feedback
    from naiyue_test_agent.application.memory.memory_wiki_protocol import Finish
    from memory_wiki_helpers import proposed

    selected = {'P1': {'evidence_ids':['E1']}, 'P2': {'evidence_ids':['E2']}}
    proposal = {k:v for k,v in proposed().items() if k not in ('matter','scope','topic','sources','scope_evidence_ids')}
    plan = Finish.model_validate(dict(step='finish', decisions=[dict(relation='merge',
        target_old_ref='O1', old_refs=['O1'], proposal_ids=['P1','P2'], evidence_ids=['E1'], proposal=proposal)]))
    assert 'evidence_ids' in str(finish_feedback(plan, selected))
    plan.decisions[0].evidence_ids.append('E2')
    assert finish_feedback(plan, selected) == []


def test_duplicated_new_page_ref_gets_parameter_feedback():
    from naiyue_test_agent.application.memory.memory_organization_feedback import finish_feedback
    from naiyue_test_agent.application.memory.memory_wiki_protocol import Finish

    page = dict(ref='N1', topic='working_agreements', title='环境', description='')
    plan = Finish.model_validate(dict(step='finish', decisions=[], page_changes=[page,page]))
    assert 'page_changes' in str(finish_feedback(plan, {}))
