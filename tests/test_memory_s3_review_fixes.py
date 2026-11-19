"""Regression coverage for S3 group isolation and inherited update fields."""
import asyncio
import json
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from test_memory_batches import archive, save
from test_memory_job_runner import fixture
from test_memory_proposal_comparison import prepared, decision, group
from test_async_application import StreamingToolModel
from test_memory_readonly import memory
from test_memory_lifecycle import source
from naiyue_test_agent.application.memory.memory_proposals import compile_comparison_decisions
from naiyue_test_agent.application.memory.memory_job_runner import drive


def old_row():
    return dict(memory_id='a'*32,version=1,scope='workspace',kind='context',sources=[],
        evidence_at=1000.,target='pytest',machine=None,module=None,subject='pytest环境',
        body='旧环境',title='测试',reason='旧证据',aliases=[],gaps=[],tags=[],deadline_at=2000000000.)


def test_tool_correction_inherits_target_before_time_gate():
    p=prepared();p['evidence'][0].update(user_origin=False,role='tool',tool_status='success')
    p['proposal']['basis']='tool_observation'
    claim=dict(kind='context',subject='pytest环境',body='新环境',basis='tool_observation',intent='activate')
    writes=compile_comparison_decisions(dict(group_id='G1',decisions=[decision(relation='correction',
        target_old_ref='O1',claim=claim)]),group(old_refs=['O1']),{'P1':p},{'O1':old_row()})
    assert writes[0][0]['proposal']['target']=='pytest'


def test_inherited_deadline_is_not_redeclared_as_new_user_instruction():
    old=old_row()
    writes=compile_comparison_decisions(dict(group_id='G1',decisions=[decision(relation='supplement',
        target_old_ref='O1')]),group(old_refs=['O1']),{'P1':prepared()},{'O1':old})
    assert writes[0][0]['proposal']['deadline'] is None


@pytest.mark.parametrize('bad_id', [[], {}])
def test_bad_group_id_preserves_independent_valid_group(archive, tmp_path, bad_id):
    from test_memory_proposal_comparison import _wiki_worker_case, _wiki_extract, _two_wiki_proposals, _second_topic_add

    async def run():
        invalid = dict(result=dict(step='finish', decisions=[dict(relation='unsupported',
            proposal_ids=[bad_id])]))
        replies = [_wiki_extract(proposals=_two_wiki_proposals()),
            AIMessage(content=json.dumps(invalid)), *_second_topic_add()]
        service, job, _ = _wiki_worker_case(archive, tmp_path, replies)
        try:
            await drive(service, job)
            assert job.saved == 1 and job.failed == 1 and job.result_status == 'partial'
            assert len(job.model.seen) == 4 and not job.model.replies
            assert 'wiki_organization_failed' in job.skipped
            assert archive.database.connection.execute('SELECT body FROM naiyue_memories').fetchone()[0] == '报告使用中文'
        finally:
            service.memory.close()
    asyncio.run(run())


def test_version_changed_proposal_invalidates_shared_group(memory):
    import time
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    from naiyue_test_agent.contracts.memory import MemoryReadError
    from test_memory_wiki_organization import prepared

    storage, access, workspace, items, ledger = prepared(memory)
    pid = items[0]['page_id']
    with storage.memory.wiki_edit('workspace', workspace, now=time.time(), capacity=30) as writer:
        page = writer.page(pid)
        writer.apply(pages=[dict(page, expected_version=page['version'], title='更新后的页')])
    result = dict(step='finish', decisions=[
        dict(relation='move', old_refs=[ref], target_old_ref=ref, destination='N1') for ref in ('O1', 'O2')],
        page_changes=[dict(ref='N1', topic='working_agreements', title='移动后的页', description='')])
    before = storage.database.connection.total_changes
    with pytest.raises(MemoryReadError, match='memory_conflict'):
        commit_group(access, 'caller', workspace, [(ledger, {}, result)], lambda _: True)
    assert storage.database.connection.total_changes == before
    assert sorted(r[0] for r in storage.database.connection.execute('SELECT version FROM naiyue_memories')) == [1, 1]


def test_budget_skip_reports_partial_without_finishing_organization(archive, tmp_path, monkeypatch):
    from test_memory_proposal_comparison import _wiki_worker_case, _wiki_extract
    from test_memory_wiki_worker import read
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger

    async def run():
        service, job, events = _wiki_worker_case(archive, tmp_path, [_wiki_extract(),
            read(dict(action='list_pages', topic='working_agreements'))])
        service.settings = service.memory.settings = service.settings.model_copy(update={'comparison_material_bytes': 4096})
        original = Ledger.read

        def over_budget(*args, **kwargs):
            return dict(original(*args, **kwargs), content='x'*5000)

        monkeypatch.setattr(Ledger, 'read', over_budget)
        try:
            await drive(service, job)
            assert job.result_status == 'partial' and job.failed == 1
            assert job.saved == 0 and job.overview_saved == 1
            assert len(job.model.seen) == 2 and not job.model.replies
            assert job.skipped == ['wiki_organization_failed']
            assert [e.data.status for e in events if e.event_type == 'run_finished'] == ['failed']
        finally:
            service.memory.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['confirm', 'supplement'])
def test_tool_evidence_update_preserves_deadline_in_real_store(memory, mode):
    from test_memory_wiki_evidence_gates import tool_source
    from test_memory_wiki_mutations import args, call, setup

    storage, access, _ = setup(memory)
    user = source(access, 'caller', at=1000.)
    tool = tool_source(access, 'bash', at=1001.)
    request = args(sources=[user, tool])
    request['proposal'].update(basis='tool_observation', target='pytest', deadline=2000000.)
    old = call(memory, request, now=1002.)['item']
    fresh = tool_source(access, 'bash', at=2000.)
    saved = call(memory, dict(action='update', scope='workspace', memory_id=old['memory_id'],
        expected_version=1, change=dict(mode=mode), sources=[fresh]), now=2001.)['item']
    row = storage.database.connection.execute('SELECT deadline_at,target,body FROM naiyue_memories WHERE memory_id=?',
        (old['memory_id'],)).fetchone()
    assert tuple(row) == (2000000., 'pytest', request['proposal']['body'])
    assert saved['version'] == 2 and saved['expires_at'] == row['deadline_at']
    assert saved['evidence_at'] == (2000. if mode == 'confirm' else old['evidence_at'])


def test_extraction_request_includes_exact_wiki_schema(archive, tmp_path):
    from naiyue_test_agent.application.memory.memory_job_runner import _prepare
    from naiyue_test_agent.application.memory.memory_wiki_protocol import WikiExtractionResult

    async def run():
        save(archive, [HumanMessage(id='u', content='以后使用.venv', additional_kwargs={'naiyue_human_input': True})])
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[]))
        try:
            _, messages, _, _, _ = await _prepare(service, job, 1)
            schema = json.loads(messages[0].content.split('Output JSON schema:\n', 1)[1])
            assert schema == WikiExtractionResult.model_json_schema()
            fields = schema['$defs']['Extraction']['properties']
            assert fields['scope_evidence_ids']['items']['type'] == 'string'
            assert 'kind' not in fields and 'identity_terms' not in fields
        finally:
            service.memory.close()
    asyncio.run(run())


def test_extraction_schema_cost_is_counted_before_reading_archive(archive, tmp_path, monkeypatch):
    from langchain_core.messages import SystemMessage
    from langchain_core.messages.utils import count_tokens_approximately
    from naiyue_test_agent.application.memory import memory_job_runner as runner
    from naiyue_test_agent.application.memory.memory_wiki_protocol import EXTRACT_PROMPT
    from naiyue_test_agent.contracts.memory import MemoryReadError

    async def run():
        save(archive, [HumanMessage(id='u', content='以后使用.venv', additional_kwargs={'naiyue_human_input': True})])
        without_schema = SystemMessage(content=EXTRACT_PROMPT.format(overview_chars=1200, max_proposals=8))
        cap = count_tokens_approximately(runner._messages(without_schema, None, {}, None)) + 128
        service, job, _ = fixture(archive, tmp_path, StreamingToolModel(replies=[]), max_input_tokens=cap)
        calls = []

        def observed(*args, **kwargs):
            calls.append(True)
            raise AssertionError('archive read before budget validation')

        monkeypatch.setattr(runner, 'build_batch', observed)
        try:
            with pytest.raises(MemoryReadError, match='memory_batch_budget'):
                await runner._prepare(service, job, 1)
            assert not calls and not job.model.seen
        finally:
            service.memory.close()
    asyncio.run(run())
