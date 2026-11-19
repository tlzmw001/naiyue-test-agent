"""S1 behavior before production integration: exact source spans, never guessed quotes."""
import pytest
from langchain_core.messages import HumanMessage
from test_memory_batches import archive, save, evidence_batch


def build(store, **kw):
    from naiyue_test_agent.application.memory.memory_batches import build_batch
    return build_batch(store.memory, **(dict(session_id='session', thread_id='root-thread',
        conversation_revision=0, start=(0,0), upper=100, token_budget=16000, source_chars=5,
        max_messages=128, max_read_chars=65536, overlap_chars=600, context_floor=1) | kw))


def test_pages_are_joined_and_duplicate_sentences_have_distinct_evidence(archive):
    save(archive, [HumanMessage(id='u', content='以后本地用.venv。CI保持原样。以后本地用.venv。',
        additional_kwargs={'naiyue_human_input':True})])
    batch = build(archive)
    assert [u['text'] for u in batch['units']] == ['以后本地用.venv。','CI保持原样。','以后本地用.venv。']
    assert len(batch['evidence_map']) == 3
    assert [u['start_offset'] for u in batch['units']] == [0, 11, 18]
    assert batch['complete']


def test_binder_uses_program_offsets_and_rejects_forged_or_duplicate_ids(archive):
    from naiyue_test_agent.application.memory.memory_batches import bind_proposal_evidence
    from naiyue_test_agent.contracts.memory import MemoryReadError
    save(archive, [HumanMessage(id='u', content='使用.venv。使用.venv。')])
    batch = build(archive)
    value = bind_proposal_evidence({'sources':[{'evidence_id':'E2'}]}, batch)
    assert value['sources'][0]['quote'] == '使用.venv。'
    assert value['sources'][0]['offset'] == len('使用.venv。')
    for ids in (['E999'], ['E1','E1']):
        with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
            bind_proposal_evidence({'sources':[{'evidence_id':i} for i in ids]}, batch)


def test_hard_cut_never_gets_writable_evidence(archive):
    save(archive,[HumanMessage(id='u',content='x'*1000)])
    batch=build(archive)
    assert batch['units'] and not batch['evidence_map']
    assert all(u['context_only'] for u in batch['units'])


def test_limits_reject_bool_and_invalid_overlap(archive):
    from naiyue_test_agent.contracts.memory import MemoryReadError
    for kw in (dict(max_messages=True),dict(max_read_chars=1024,overlap_chars=600)):
        with pytest.raises(MemoryReadError,match='memory_batch_position_invalid'):
            build(archive,**kw)



def test_read_cap_cannot_claim_unread_tail_complete(archive):
    save(archive,[HumanMessage(id='u',content='完整自然句。'*500)])
    batch=build(archive,max_read_chars=1024,overlap_chars=0,token_budget=128000)
    assert not batch['complete']
    assert batch['next_position']==[1,1024]
    assert max(u['end_offset'] for u in batch['units'])==1024


def test_message_continuation_keeps_bounded_original_overlap(archive):
    from naiyue_test_agent.application.memory.memory_batches import bind_proposal_evidence
    from naiyue_test_agent.contracts.memory import MemoryReadError
    prefix='只用于本地。'
    save(archive,[HumanMessage(id='u',content=prefix+'以后用.venv。')])
    batch=build(archive,start=(1,len(prefix)))
    prior=[u for u in batch['units'] if u['origin']=='overlap']
    assert ''.join(u['text'] for u in prior)==prefix
    assert batch['coverage']['read_ranges']==[(1,len(prefix),len(prefix+'以后用.venv。'))]
    with pytest.raises(MemoryReadError,match='memory_proposal_invalid'):
        bind_proposal_evidence({'sources':[{'evidence_id':prior[0]['evidence_id']}]},batch)


def test_missing_tool_parent_is_context_only(archive):
    from langchain_core.messages import ToolMessage
    save(archive,[ToolMessage(id='t',content='18 passed。',tool_call_id='orphan',name='test')])
    batch=build(archive)
    assert batch['units'][0]['tool_pair_status']=='missing'
    assert not batch['evidence_map']


def test_settings_keep_trigger_thresholds_and_enforce_overlap():
    from naiyue_test_agent.memory_settings import MemorySettings
    from pydantic import ValidationError
    settings=MemorySettings()
    assert (settings.trigger_turns,settings.trigger_chars)==(8,20000)
    for kwargs in (dict(batch_max_messages=True),dict(batch_read_chars=1024,batch_overlap_chars=600)):
        with pytest.raises(ValidationError):MemorySettings(**kwargs)


def test_natural_scanner_keeps_multiline_quotes_and_code_lines():
    from naiyue_test_agent.application.memory.memory_input_units import spans
    text='他说“临时\n不要长期使用”。结束。'
    parts=spans(text)
    assert [p[1] for p in parts]==['他说“临时\n不要长期使用”。','结束。']
    code='```python\nx=1.5\n```\n结束。'
    assert ''.join(p[1] for p in spans(code))==code
    assert all(p[2] for p in spans(code))


def test_tool_binding_keeps_parent_context(archive):
    from scripts.memory_evaluation_cases import seed_sources
    from naiyue_test_agent.application.memory.memory_batches import bind_proposal_evidence
    seed_sources(archive,'session',[dict(id='u',role='user',text='检查本地测试。'),
        dict(id='t',role='tool',text='18 passed。',tool_call_id='call-7')],at=1000.)
    batch=build(archive)
    result=bind_proposal_evidence({'sources':[{'evidence_id':next(u['evidence_id'] for u in batch['units'] if u['role']=='tool')}]},batch)
    assert any(u['role']=='ai' and u['tool_call_id']=='call-7' for u in result['context_units'])
    header=next(u for u in result['context_units'] if u['role']=='ai')
    assert header['occurred_at'] < result['evidence'][0]['occurred_at']
    assert all('args' not in u for u in batch['units'])


def test_oversized_turn_uses_complete_messages_before_splitting(archive):
    from scripts.memory_evaluation_cases import seed_sources
    seed_sources(archive,'session',[dict(id='u',role='user',text='本地回归用.venv。'),
        dict(id='a',role='assistant',text='检查记录。'*100)],at=1000.)
    batch=build(archive,token_budget=1200)
    assert {u['message_id'] for u in batch['units'] if u['origin']=='new'}=={'u'}
    assert batch['next_position']==[2,0]


@pytest.mark.parametrize('size,citable',[(300,True),(301,False)])
def test_exact_source_quote_limit(size,citable,archive):
    save(archive,[HumanMessage(id='u',content='字'*size)])
    batch=build(archive)
    assert bool(batch['evidence_map']) is citable
    assert ''.join(u['text'] for u in batch['units'])=='字'*size
    assert all(len(u['text'])<=300 for u in batch['units'])


def test_context_is_not_automatically_added_to_formal_sources(archive):
    from naiyue_test_agent.application.memory.memory_batches import bind_proposal_evidence
    save(archive,[HumanMessage(id='u',content='以后用.venv。只用于本地。')])
    batch=build(archive)
    result=bind_proposal_evidence({'sources':[{'evidence_id':'E1'}]},batch)
    assert result['context_complete']
    assert [u['text'] for u in result['context_units']]==['只用于本地。']
    assert [s['quote'] for s in result['sources']]==['以后用.venv。']


def test_orphan_tool_overlap_stays_non_citable(archive):
    from langchain_core.messages import ToolMessage
    save(archive,[ToolMessage(id='t',content='18 passed。继续日志。',tool_call_id='missing',name='test'),
        HumanMessage(id='u',content='检查完成。')])
    batch=build(archive,start=(1,len('18 passed。')),upper=2)
    assert all(u['evidence_id'] is None for u in batch['units'] if u['role']=='tool')


def test_call_header_does_not_skip_tool_body(archive):
    from scripts.memory_evaluation_cases import seed_sources
    seed_sources(archive,'session',[dict(id='u',role='user',text='检查测试。'),
        dict(id='t',role='tool',text='测试通过。'*150,tool_call_id='c')],at=1000.)
    batch=build(archive,upper=3,token_budget=1200,overlap_chars=0)
    assert batch['next_position'][0]<=3
    assert not batch['complete']


def test_tool_only_proposal_keeps_matter_and_routes_to_wiki_topic(archive):
    from naiyue_test_agent.application.memory.memory_wiki_protocol import bind_extraction
    from scripts.memory_evaluation_cases import seed_sources
    seed_sources(archive,'session',[dict(id='u',role='user',text='检查测试。'),
        dict(id='t',role='tool',text='测试通过。',tool_call_id='c')],at=1000.)
    batch=build(archive,start=(3,0),upper=3,context_floor=3)
    eid=next(u['evidence_id'] for u in batch['units'] if u['role']=='tool' and u['evidence_id'])
    raw = dict(matter='本地测试结果', topic='stable_knowledge', title='测试观察', subject='测试',
        body='测试通过', basis='tool_observation', intent='activate', target='本地测试',
        reason='保留可复用观察', sources=[dict(evidence_id=eid)])
    bound = bind_extraction(raw, batch, 1)
    assert (bound['matter'], bound['scope'], bound['topic']) == ('本地测试结果', 'workspace', 'stable_knowledge')
    assert bound['sources'][0]['message_id'] == 't' and bound['sources'][0]['quote'] == '测试通过。'
    from naiyue_test_agent.contracts.memory import MemoryReadError
    with pytest.raises(MemoryReadError, match='memory_proposal_invalid'):
        bind_extraction(dict(raw, matter=''), batch, 1)


@pytest.mark.parametrize('opening,closing',[('“','”'),('（','）'),('```','```')])
def test_continuation_does_not_promote_unknown_parser_state(archive,opening,closing):
    text='反面示例'+opening+'字'*1020+'第一句。危险操作。结束'+closing+'保留数据。'
    save(archive,[HumanMessage(id='u',content=text)])
    first=build(archive,upper=1,max_read_chars=1024,overlap_chars=200,token_budget=20000)
    second=build(archive,upper=1,start=first['next_position'],max_read_chars=1024,overlap_chars=200,token_budget=20000)
    assert not any(u['evidence_id'] for u in second['units'] if u['origin']=='new')
