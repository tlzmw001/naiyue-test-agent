"""Regression coverage for consolidation gates and decision isolation."""
import time
import pytest
from test_memory_readonly import memory
from test_memory_wiki_organization import prepared
from test_memory_wiki_mutations import args
from test_memory_lifecycle import source
from naiyue_test_agent.application.memory.memory_organization_commit import commit_group
from naiyue_test_agent.contracts.memory import MemoryReadError
from naiyue_test_agent.tools.memory_wiki_actions import WikiProposal


def test_merge_rejects_expired_new_proposal(memory):
    storage,access,workspace,items,ledger=prepared(memory,1)
    p=WikiProposal.model_validate(dict(args(sources=[])['proposal'],body='过期的新约定',subject='同一事项')).model_dump()
    ref=source(access,'caller',at=time.time()-31*86400)
    bound=dict(proposal=p,evidence_ids=['E1'],sources=[ref],scope_evidence_ids=[],evidence=[dict(evidence_id='E1',origin='new')])
    decision=dict(relation='merge',old_refs=['O1'],target_old_ref='O1',proposal=p,proposal_ids=['P1'],evidence_ids=['E1'])
    with pytest.raises(MemoryReadError,match='memory_evidence_expired'):
        commit_group(access,'caller',workspace,[(ledger,dict(P1=bound),dict(step='finish',decisions=[decision]))],lambda _:True)
    assert storage.database.connection.execute('SELECT version FROM naiyue_memories').fetchone()[0]==1


@pytest.mark.parametrize('kind',['metadata','links'])
def test_page_writes_require_permission(memory,kind):
    storage,access,workspace,items,ledger=prepared(memory,1)
    finished=dict(step='finish',decisions=[])
    if kind=='metadata':finished['page_changes']=[dict(ref=items[0]['page_id'],topic='working_agreements',title='拒绝',description='')]
    else:finished['links']=[dict(page=items[0]['page_id'],targets=[])]
    with pytest.raises(MemoryReadError,match='memory_permission_denied'):
        commit_group(access,'caller',workspace,[(ledger,{},finished)],lambda _:False)
    assert storage.database.connection.execute('SELECT version FROM naiyue_memory_pages').fetchone()[0]==1


def test_independent_decisions_in_one_read_unit_split(memory):
    from naiyue_test_agent.application.memory.memory_organization_groups import connected_groups
    _,_,_,items,ledger=prepared(memory,1)
    p={'P1':{},'P2':{}}
    finished=dict(step='finish',decisions=[dict(relation='add',proposal_ids=['P1'],destination='N1'),dict(relation='add',proposal_ids=['P2'],destination='N2')],page_changes=[dict(ref='N1')],links=[])
    groups=connected_groups([dict(ledger=ledger,proposals=p,finished=finished)])
    assert len(groups)==2
    assert sorted(len(g[0]['finished']['decisions']) for g in groups)==[1,1]


def test_merge_cannot_borrow_user_basis_for_failed_tool(memory):
    from test_memory_wiki_evidence_gates import tool_source
    storage,access,workspace,items,ledger=prepared(memory,1)
    p=WikiProposal.model_validate(dict(args(sources=[])['proposal'],body='工具声称的新约定',subject='同一事项')).model_dump()
    ref=tool_source(access,'bash',at=time.time(),status='error')
    bound=dict(proposal=p,evidence_ids=['E1'],sources=[ref],scope_evidence_ids=[],evidence=[dict(evidence_id='E1',origin='new')])
    decision=dict(relation='merge',old_refs=['O1'],target_old_ref='O1',proposal=p,proposal_ids=['P1'],evidence_ids=['E1'])
    with pytest.raises(MemoryReadError,match='memory_direct_source_required'):
        commit_group(access,'caller',workspace,[(ledger,dict(P1=bound),dict(step='finish',decisions=[decision]))],lambda _:True)
    assert storage.database.connection.execute('SELECT version FROM naiyue_memories').fetchone()[0]==1


def test_unchanged_single_proposal_add_inherits_its_bound_sources(memory):
    from naiyue_test_agent.application.memory.memory_organization_reads import Ledger
    from test_memory_wiki_mutations import setup
    storage,access,workspace=setup(memory)
    ref=source(access,'caller',at=time.time()-1)
    p=WikiProposal.model_validate(args(sources=[])['proposal']).model_dump()
    bound=dict(proposal=p,evidence_ids=['E1'],sources=[ref],scope_evidence_ids=[],evidence=[dict(evidence_id='E1',origin='new')])
    ledger=Ledger('workspace','working_agreements')
    ledger.read(access,'caller',workspace,dict(action='list_pages',topic='working_agreements'),lambda _:True)
    finished=dict(step='finish',decisions=[dict(relation='add',proposal_ids=['P1'],destination='N1')],
        page_changes=[dict(ref='N1',topic='working_agreements',title='环境',description='')])
    assert commit_group(access,'caller',workspace,[(ledger,dict(P1=bound),finished)],lambda _:True)==1
    row=storage.database.connection.execute('SELECT body,version FROM naiyue_memories').fetchone()
    assert row['body']==p['body'] and row['version']==1
    assert storage.database.connection.execute('SELECT COUNT(*) FROM naiyue_memory_sources').fetchone()[0]==1
