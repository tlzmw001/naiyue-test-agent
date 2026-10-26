"""The durable admission record, rather than an acknowledgement, owns execution."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest


def policy(**changes):
    values=dict(max_operation_records=20,max_active_operations=4,process_output_bytes=8,
        total_output_bytes=12,max_message_bytes=65536)
    return SimpleNamespace(**(values|changes))


def test_operation_reservation_is_single_flight_and_conflicts_are_explicit(tmp_path):
    from naiyue_test_agent.storage.sandbox_journal import SandboxJournal
    from naiyue_test_agent.contracts.sandbox import SandboxError
    with SandboxJournal(tmp_path/'journal.sqlite',{'binding':'B'},policy()) as journal:
        def reserve(_):return journal.reserve('a'*32,'shell',{'command':'one'}, {})[1]
        with ThreadPoolExecutor(4) as pool:assert sum(pool.map(reserve,range(8)))==1
        with pytest.raises(SandboxError,match='env_operation_conflict'):
            journal.reserve('a'*32,'shell',{'command':'two'}, {})
        journal.finish('a'*32,'completed',{'ok':True,'value':'original'})
        row,new=journal.reserve('a'*32,'shell',{'command':'one'}, {})
        assert not new and row['result']['value']=='original'


def test_restart_preserves_unknown_tombstone_and_completed_results(tmp_path):
    from naiyue_test_agent.storage.sandbox_journal import SandboxJournal
    path=tmp_path/'journal.sqlite'
    with SandboxJournal(path,{'binding':'B'},policy()) as journal:
        journal.reserve('a'*32,'write',{'subject':{},'content':'x'}, {})
        journal.append('a'*32,b'known')
        journal.reserve('b'*32,'write',{'subject':{},'content':'y'}, {})
        journal.finish('b'*32,'completed',{'ok':True,'value':['done',{}]})
    with SandboxJournal(path,{'binding':'B'},policy()) as journal:
        row,new=journal.reserve('a'*32,'write',{'subject':{},'content':'x'}, {})
        assert row['state']=='unknown' and not new
        assert journal.output('a'*32,0,100)['data']==b'known'
        assert journal.get('b'*32)['state']=='completed'


def test_output_offsets_and_both_retention_limits_report_gaps(tmp_path):
    from naiyue_test_agent.storage.sandbox_journal import SandboxJournal
    with SandboxJournal(tmp_path/'journal.sqlite',{'binding':'B'},policy()) as journal:
        for ident in ('a','b'):journal.reserve(ident*32,'shell',{}, {})
        journal.append('a'*32,b'123456');journal.append('a'*32,b'7890')
        first=journal.output('a'*32,0,100)
        assert first['gap'] and first['offset']==6 and first['data']==b'7890'
        assert first['next_offset']==10 and first['observed_bytes']==10
        assert journal.output('a'*32,10,100)['data']==b''
        journal.append('b'*32,b'abcdefgh')
        journal.append('b'*32,b'ij')
        second=journal.output('b'*32,0,100)
        assert second['gap'] and second['offset']==8 and second['data']==b'ij'
        journal.reserve('c'*32,'shell',{}, {})
        journal.append('c'*32,b'ABCDEFGH')
        evicted=journal.output('a'*32,0,100)
        assert evicted['gap'] and evicted['offset']==10 and evicted['data']==b''
        assert journal.retained_bytes()<=12


def test_record_capacity_refuses_before_side_effect_and_identity_cannot_change(tmp_path):
    from naiyue_test_agent.storage.sandbox_journal import SandboxJournal
    from naiyue_test_agent.contracts.sandbox import SandboxError
    path=tmp_path/'journal.sqlite'
    with SandboxJournal(path,{'binding':'B'},policy(max_operation_records=1)) as journal:
        journal.reserve('a'*32,'shell',{}, {})
        with pytest.raises(SandboxError,match='env_journal_full'):journal.reserve('b'*32,'shell',{}, {})
    with pytest.raises(SandboxError,match='env_binding_mismatch'):
        SandboxJournal(path,{'binding':'OTHER'},policy())


def test_one_large_chunk_keeps_the_bounded_tail_instead_of_discarding_everything(tmp_path):
    from naiyue_test_agent.storage.sandbox_journal import SandboxJournal
    with SandboxJournal(tmp_path/'journal.sqlite',{'binding':'B'},policy()) as journal:
        journal.reserve('a'*32,'shell',{}, {})
        journal.append('a'*32,b'0123456789abcd')
        output=journal.output('a'*32,0,100)
        assert output['gap'] and output['offset']==6 and output['data']==b'6789abcd'
        assert output['observed_bytes']==14
