"""Frozen calibration decisions exercise the production worker, not model quality."""
import asyncio
import hashlib
import json
import time
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage,HumanMessage
from test_memory_batches import archive, save, batch, result
from memory_wiki_helpers import proposed
from test_memory_job_runner import fixture
from test_async_application import StreamingToolModel
from test_memory_wiki_worker import finish, read
from naiyue_test_agent.application.memory.memory_wiki_protocol import bind_extraction
from naiyue_test_agent.application.memory.memory_job_runner import drive

# S6 versions the action contract; the frozen S3 matrix remains historical evidence.
PATH=Path(__file__).parent/'data/memory_optimization/comparison_s6.json'
assert hashlib.sha256(PATH.read_bytes()).hexdigest()==json.loads(PATH.with_name('comparison_s6_protocol.json').read_text())['fixtures_sha256']
DATA=json.loads(PATH.read_text())


def extraction(body, evidence_id):
    return proposed(matter='本地回归环境', subject='本地回归环境', body=body,
                    sources=[dict(evidence_id=evidence_id)])


@pytest.mark.parametrize('case', DATA['cases'], ids=lambda c: c['id'])
def test_fixed_comparison_decisions(case, archive, tmp_path):
    async def run():
        now = time.time()
        archive.memory.prepare_wiki(now=now)
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[]))
        try:
            first = None
            if case['old_body']:
                at = now - case['old_age']
                save(archive, [HumanMessage(id='old', content=case['old_body'],
                    additional_kwargs={'naiyue_human_input': True})], occurred_at=at)
                bound = bind_extraction(extraction(case['old_body'], 'E1'), batch(archive), 1)
                page = dict(topic='working_agreements', title='本地回归环境', description='')
                request = dict(action='record', scope='workspace', destination={'new_page': page},
                               proposal=bound['proposal'], sources=bound['sources'])
                first = service.memory.query_wiki('session', job.project_id, request, now=at)['item']
            text = case['claim_body'] or '今天暂时试试本地回归，尚未确定长期选择。'
            save(archive, [HumanMessage(id='new', content=text,
                additional_kwargs={'naiyue_human_input': True})], occurred_at=now-case['new_age'])
            eid = next(u['evidence_id'] for u in batch(archive)['units'] if u['message_id'] == 'new')
            proposal = extraction(text, eid)
            relation = case['relation']
            if relation == 'supplement' and case['confirmation'] == 'whole':
                relation = 'confirm'
            decision = dict(proposal_ids=['P1'], relation=relation, confirmation_scope=case['confirmation'])
            replies = [AIMessage(content=json.dumps(result(proposals=[proposal])))]
            expired = case['id'] == 'expired_partial'
            if first:
                replies.extend([
                    read(dict(action='list_pages', topic='working_agreements')),
                    read(dict(action='read_page', page_id=first['page_id'])),
                ])
                decision.update(old_refs=['O1'], target_old_ref='O1')
                if relation != 'duplicate':
                    decision['evidence_ids'] = [eid]
            if relation == 'correction':
                decision['proposal'] = bind_extraction(proposal, batch(archive), 1)['proposal']
            # Unlike the legacy comparison result, expired pages cannot yield an O reference.
            if not expired:
                replies.append(finish(decisions=[decision]))
            expected_calls = len(replies)
            job.model.replies = replies
            job.upper = 2 if first else 1
            await drive(service, job)
            assert job.saved == (0 if expired else case['expected_writes'])
            assert job.failed == (1 if expired else case['expected_failed'])
            assert job.overview_saved == 1 and len(job.model.seen) == expected_calls
            assert not job.model.replies
            rows = archive.database.connection.execute('SELECT * FROM naiyue_memories').fetchall()
            assert len(rows) == case['expected_rows']
            if first:
                row, = rows
                assert row['memory_id'] == first['memory_id']
                assert row['version'] == (1 if expired else case['expected_version'])
                assert (row['expires_at'] > first['expires_at']) is case['renewed']
                if relation != 'correction' or case['expected_failed']:
                    assert row['body'] == case['old_body']
                else:
                    assert row['body'] == case['claim_body']
                if case['confirmation'] in ('partial', 'unknown'):
                    assert row['expires_at'] == first['expires_at']
                    assert row['evidence_at'] == row['last_confirmed_at'] == first['evidence_at']
                if expired:
                    assert row['expires_at'] < now
                    assert 'wiki_organization_failed' in job.skipped
        finally:
            service.memory.close()
    asyncio.run(run())
