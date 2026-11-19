"""Organization requires complete reads and atomic, source-preserving writes."""
import time
import pytest
from naiyue_test_agent.contracts.memory import MemoryReadError
from test_memory_readonly import memory
from test_memory_wiki_mutations import setup,args,call
from test_memory_lifecycle import source


def test_ledger_requires_discovery_and_complete_read(memory):
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    _,access,workspace=setup(memory);now=time.time()
    item=call(memory,args(sources=[source(access,'caller',at=now-1)]),now=now)['item']
    ledger=Ledger('workspace','working_agreements')
    read=dict(action='read_page',scope='workspace',page_id=item['page_id'])
    with pytest.raises(MemoryReadError,match='memory_page_not_discovered'):
        ledger.read(access,'caller',workspace,read,lambda _:True)
    ledger=Ledger('workspace','working_agreements')
    ledger.read(access,'caller',workspace,dict(action='list_pages',scope='workspace',topic='working_agreements'),lambda _:True)
    ledger.read(access,'caller',workspace,read,lambda _:True)
    assert ledger.require_page(item['page_id']) and ledger.old['O1']['memory_id']==item['memory_id']


def test_organize_move_retains_id_sources_and_clock(memory):
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    _,access,workspace=setup(memory);now=time.time()
    item=call(memory,args(sources=[source(access,'caller',at=now-1)]),now=now)['item']
    ledger=Ledger('workspace','working_agreements',writable=[item['page_id']])
    ledger.read(access,'caller',workspace,dict(action='list_pages',scope='workspace',topic='working_agreements'),lambda _:True)
    ledger.read(access,'caller',workspace,dict(action='read_page',scope='workspace',page_id=item['page_id']),lambda _:True)
    finish=dict(step='finish',decisions=[dict(relation='move',old_refs=['O1'],target_old_ref='O1',destination='N1')],
        page_changes=[dict(ref='N1',topic='working_agreements',title='新页',description='分组')],links=[])
    count=commit_group(access,'caller',workspace,[(ledger,{},finish)],lambda _:True)
    assert count==1
    row=memory[0].database.connection.execute('SELECT * FROM naiyue_memories').fetchone()
    assert row['memory_id']==item['memory_id'] and row['expires_at']==item['expires_at'] and row['page_id']!=item['page_id']
    assert memory[0].memory.wiki_sources([(row['memory_id'],row['version'])])


def prepared(memory,count=2):
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    storage,access,workspace=setup(memory);now=time.time();items=[]
    for i in range(count):
        request=args(sources=[source(access,'caller',text=f'长期规则{i}',at=now-100+i)])
        request['proposal'].update(body=f'规则{i}',subject='同一事项' if i<2 else f'事项{i}')
        if items:request['destination']=dict(page_id=items[0]['page_id'],expected_page_version=i)
        items.append(call(memory,request,now=now)['item'])
    ledger=Ledger('workspace','working_agreements',writable=[items[0]['page_id']])
    result=ledger.read(access,'caller',workspace,dict(action='list_pages',scope='workspace',topic='working_agreements'),lambda _:True)
    result=ledger.read(access,'caller',workspace,dict(action='read_page',scope='workspace',page_id=items[0]['page_id']),lambda _:True)
    while result['has_more']:
        result=ledger.read(access,'caller',workspace,dict(action='read_page',scope='workspace',page_id=items[0]['page_id'],cursor=result['next_cursor']),lambda _:True)
    return storage,access,workspace,items,ledger


def test_merge_preserves_sources_earliest_expiry_and_candidate(memory):
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    storage,access,workspace,items,ledger=prepared(memory)
    proposal=dict(args(sources=[])['proposal'],body='整合规则0和规则1',subject='同一事项')
    finished=dict(step='finish',decisions=[dict(relation='merge',old_refs=['O1','O2'],target_old_ref='O1',proposal=proposal)])
    commit_group(access,'caller',workspace,[(ledger,{},finished)],lambda _:True)
    row=storage.database.connection.execute("SELECT * FROM naiyue_memories WHERE status!='inactive'").fetchone()
    assert row['body']=='整合规则0和规则1' and row['status']=='candidate'
    assert row['expires_at']==min(i['expires_at'] for i in items)
    assert len(storage.memory.wiki_sources([(row['memory_id'],row['version'])]))==2
    assert storage.database.connection.execute("SELECT COUNT(*) FROM naiyue_memories WHERE status='inactive'").fetchone()[0]==1


@pytest.mark.parametrize('failure',['stale','unread','denied','full','wrong_scope'])
def test_group_failure_rolls_back_all_changes(memory,failure):
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    storage,access,workspace,items,ledger=prepared(memory)
    changes=[dict(ref='N1',topic='working_agreements',title='拆分页',description='')]
    decisions=[dict(relation='move',old_refs=['O1'],target_old_ref='O1',destination='N1'),
        dict(relation='move',old_refs=['O2'],target_old_ref='O2',destination='N1')]
    if failure=='stale':
        with storage.memory.wiki_edit('workspace',workspace,now=time.time(),capacity=30) as writer:
            page=writer.page(items[0]['page_id']);writer.apply(pages=[dict(page,expected_version=page['version'],title='已变更')])
    if failure=='unread':ledger.pages[items[0]['page_id']]['complete']=False
    if failure=='full':access.settings=access.settings.model_copy(update={'wiki_topic_max_memories':1})
    if failure=='wrong_scope':ledger.scope='personal'
    before=[tuple(r) for r in storage.database.connection.execute('SELECT memory_id,version,page_id,expires_at FROM naiyue_memories')]
    with pytest.raises(MemoryReadError):
        commit_group(access,'caller',workspace,[(ledger,{},dict(step='finish',decisions=decisions,page_changes=changes))],lambda _:failure!='denied')
    assert [tuple(r) for r in storage.database.connection.execute('SELECT memory_id,version,page_id,expires_at FROM naiyue_memories')]==before
    assert storage.database.connection.execute("SELECT COUNT(*) FROM naiyue_memory_pages WHERE title='拆分页'").fetchone()[0]==0


def test_duplicate_is_zero_writes_and_never_renews(memory):
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    storage,access,workspace,items,ledger=prepared(memory,1)
    before=storage.database.connection.total_changes
    count=commit_group(access,'caller',workspace,[(ledger,{},dict(step='finish',decisions=[dict(relation='duplicate',old_refs=['O1'],target_old_ref='O1')]))],lambda _:True)
    assert count==0 and storage.database.connection.total_changes==before


def test_cannot_create_page_before_complete_directory(memory):
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    storage,access,workspace,items,ledger=prepared(memory,1)
    ledger.directories.clear()
    with pytest.raises(MemoryReadError,match='memory_read_incomplete'):
        commit_group(access,'caller',workspace,[(ledger,{},dict(step='finish',decisions=[],page_changes=[
            dict(ref='N1',topic='working_agreements',title='绕过目录',description='')]))],lambda _:True)
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memory_pages').fetchone()[0]==1


def test_two_adds_in_one_group_cannot_bypass_duplicate_gate(memory):
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    storage,access,workspace=setup(memory);now=time.time()
    ref=source(access,'caller',at=now-1)
    ledger=Ledger('workspace','working_agreements')
    ledger.read(access,'caller',workspace,dict(action='list_pages',scope='workspace',topic='working_agreements'),lambda _:True)
    from naiyue_test_agent.tools.memory_wiki_actions import WikiProposal
    p=WikiProposal.model_validate(args(sources=[])['proposal']).model_dump()
    bound=dict(proposal=p,evidence_ids=['E1'],sources=[ref],scope_evidence_ids=[],evidence=[dict(evidence_id='E1',origin='new')])
    decisions=[dict(relation='add',proposal_ids=[pid],destination='N1',evidence_ids=['E1']) for pid in ('P1','P2')]
    with pytest.raises(MemoryReadError,match='memory_duplicate'):
        commit_group(access,'caller',workspace,[(ledger,dict(P1=bound,P2=bound),dict(step='finish',decisions=decisions,
            page_changes=[dict(ref='N1',topic='working_agreements',title='规则',description='')]))],lambda _:True)
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memories').fetchone()[0]==0


@pytest.mark.parametrize('whole',[False,True])
def test_merge_renews_only_with_new_whole_confirmation(memory,whole):
    from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
    from naiyue_test_agent.tools.memory_wiki_actions import WikiProposal
    storage,access,workspace,items,ledger=prepared(memory)
    now=time.time();ref=source(access,'caller',text='完整确认合并后的全部约定',at=now)
    p=WikiProposal.model_validate(dict(args(sources=[])['proposal'],body='整合规则',subject='同一事项')).model_dump()
    bound=dict(proposal=p,evidence_ids=['E1'],sources=[ref],scope_evidence_ids=[],evidence=[dict(evidence_id='E1',origin='new')])
    decision=dict(relation='merge',old_refs=['O1','O2'],target_old_ref='O1',proposal=p,proposal_ids=['P1'],
        evidence_ids=['E1'],confirmation_scope='whole' if whole else 'unknown')
    commit_group(access,'caller',workspace,[(ledger,dict(P1=bound),dict(step='finish',decisions=[decision]))],lambda _:True)
    row=storage.database.connection.execute("SELECT * FROM naiyue_memories WHERE status!='inactive'").fetchone()
    assert row['evidence_at']==(now if whole else min(i['evidence_at'] for i in items))
    assert row['status']==('active' if whole else 'candidate')
    assert len(storage.memory.wiki_sources([(row['memory_id'],row['version'])]))==3


def test_ledger_paginates_candidates_and_returns_every_old_reference(memory):
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    storage,access,workspace,items,_=prepared(memory,12)
    ledger=Ledger('workspace','working_agreements');seen=set()
    ledger.read(access,'caller',workspace,dict(action='list_pages',scope='workspace',topic='working_agreements'),lambda _:True)
    cursor=None;calls=0
    while True:
        result=ledger.read(access,'caller',workspace,dict(action='read_page',scope='workspace',page_id=items[0]['page_id'],cursor=cursor),lambda _:True)
        seen.update(result['old_refs']);calls+=1
        assert result['budget']['estimated_tokens']<=2000
        if result['complete']:break
        cursor=result['next_cursor']
    assert len(seen)==12 and len(ledger.old)==12 and calls>1
    assert any(r['status']=='candidate' for r in ledger.old.values())


def test_complete_candidate_page_includes_uncertainty_reason(memory):
    import json
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    storage,access,workspace=setup(memory);now=time.time()
    request=args(sources=[source(access,'caller',at=now-1)])
    request['proposal'].update(intent='candidate',gaps=['缺少独立确认'],reason='仍需确认环境')
    item=call(memory,request,now=now)['item']
    ledger=Ledger('workspace','working_agreements')
    ledger.read(access,'caller',workspace,dict(action='list_pages',topic='working_agreements'),lambda _:True)
    result=ledger.read(access,'caller',workspace,dict(action='read_page',page_id=item['page_id']),lambda _:True)
    content=json.loads(result['content'][8:-4])
    row=content['entries'][0]
    assert row['status']=='candidate' and row['gaps']==['缺少独立确认'] and row['reason']=='仍需确认环境'
