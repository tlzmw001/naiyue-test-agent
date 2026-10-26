"""The receiving environment verifies real Git objects and installs only once."""
import base64
from pathlib import Path

import pytest

from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.sandbox import PROTOCOL_VERSION, SandboxError
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.envd.git_inputs import InputReceiver
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.sandbox_settings import SandboxSettings
from git_input_helpers import git, repository


def request(operations, kind, params):
    return operations.dispatch(dict(protocol=PROTOCOL_VERSION,request_id='request',
        environment=operations.environment.public(),session_id='S',operation=kind,params=params))


def send(operations,snapshot,directory,*,bundle=None,base=None):
    import hashlib
    data=(bundle or directory/'input.bundle').read_bytes()
    transfer_id=hashlib.sha256((snapshot.input_id+hashlib.sha256(data).hexdigest()).encode()).hexdigest()[:32]
    metadata=dict(transfer_id=transfer_id,input_id=snapshot.input_id,original_head=snapshot.original_head,
        input_snapshot=snapshot.input_snapshot,size=len(data),sha256=hashlib.sha256(data).hexdigest(),base=base)
    request(operations,'input_begin',metadata)
    request(operations,'input_chunk',dict(transfer_id=transfer_id,offset=0,data=base64.b64encode(data).decode()))
    request(operations,'input_cache',dict(transfer_id=transfer_id))
    return metadata


def test_full_input_install_uses_journal_and_repeat_preserves_later_changes(tmp_path):
    root=repository(tmp_path);(root/'app.txt').write_text('input\n')
    snapshot=SnapshotBuilder(tmp_path/'input',SandboxSettings().git).prepare(
        __import__('naiyue_test_agent.contracts.projects',fromlist=['LocalSource']).LocalSource(root),'a'*32)
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        send(operations,snapshot,tmp_path/'input')
        payload=dict(input_id=snapshot.input_id,original_head=snapshot.original_head,input_snapshot=snapshot.input_snapshot)
        row=operations.submit('b'*32,'git_input',payload,{})
        operations.wait('b'*32,10);row=operations.journal.get('b'*32)
        assert row['state']=='completed' and row['result']['value']==payload
        assert (code/'app.txt').read_text()=='input\n'
        assert git(code,'branch','--show-current').decode().strip()=='naiyue-task/'+'a'*32
        assert git(code,'rev-parse','HEAD').decode().strip()==snapshot.input_snapshot
        (code/'app.txt').write_text('agent later\n')
        assert operations.submit('b'*32,'git_input',payload,{})['state']=='completed'
        assert (code/'app.txt').read_text()=='agent later\n'


def test_chunk_resume_rejects_wrong_offset_and_checksum_before_git_import(tmp_path):
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',SandboxSettings()) as operations:
        metadata=dict(transfer_id='a'*32,input_id='b'*32,original_head='c'*40,input_snapshot='d'*40,
            size=3,sha256='e'*64,base=None)
        assert request(operations,'input_begin',metadata)['offset']==0
        chunk=dict(transfer_id='a'*32,offset=0,data=base64.b64encode(b'abc').decode())
        assert request(operations,'input_chunk',chunk)['offset']==3
        assert request(operations,'input_chunk',chunk)['offset']==3
        with pytest.raises(GitInputError,match='git_input_chunk_conflict'):
            request(operations,'input_chunk',dict(chunk,offset=1))
        with pytest.raises(GitInputError,match='git_input_corrupt'):
            request(operations,'input_cache',{'transfer_id':'a'*32})
        assert list(code.iterdir())==[]


def test_confirmed_base_supports_real_delta_bundle(tmp_path):
    from naiyue_test_agent.contracts.projects import LocalSource
    from naiyue_test_agent.execution.git_repository import GitRepository,make_bundle
    policy=SandboxSettings();root=repository(tmp_path)
    first=SnapshotBuilder(tmp_path/'first',policy.git).prepare(LocalSource(root),'a'*32)
    (root/'app.txt').write_text('delta input\n')
    second=SnapshotBuilder(tmp_path/'second',policy.git).prepare(LocalSource(root),'b'*32)
    delta=tmp_path/'delta.bundle'
    make_bundle(GitRepository(tmp_path/'second'/'objects.git',policy.git),delta,'b'*32,base=second.original_head)
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',policy) as operations:
        send(operations,first,tmp_path/'first')
        assert request(operations,'input_probe',{'original_head':second.original_head})['has_base'] is True
        send(operations,second,tmp_path/'second',bundle=delta,base=second.original_head)
        operations.submit('c'*32,'git_input',dict(input_id='b'*32,original_head=second.original_head,
            input_snapshot=second.input_snapshot),{})
        assert operations.wait('c'*32,10)
        assert operations.journal.get('c'*32)['state']=='completed'
        assert (code/'app.txt').read_text()=='delta input\n'


def test_bundle_with_unexpected_ref_is_rejected_before_checkout(tmp_path):
    from naiyue_test_agent.contracts.projects import LocalSource
    root=repository(tmp_path);policy=SandboxSettings()
    snapshot=SnapshotBuilder(tmp_path/'input',policy.git).prepare(LocalSource(root),'a'*32)
    bundle=tmp_path/'bad.bundle'
    bundle.write_bytes((tmp_path/'input'/'input.bundle').read_bytes().replace(
        b'refs/naiyue/input/'+b'a'*32,b'refs/naiyue/input/'+b'b'*32,1))
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',policy) as operations:
        with pytest.raises(GitInputError,match='git_input_invalid_bundle'):
            send(operations,snapshot,tmp_path/'input',bundle=bundle)
        assert not any(code.iterdir())


def test_installation_blocks_reads_and_new_execution_until_checkout_finishes(tmp_path,monkeypatch):
    from threading import Event
    from naiyue_test_agent.contracts.projects import LocalSource
    root=repository(tmp_path);policy=SandboxSettings()
    snapshot=SnapshotBuilder(tmp_path/'input',policy.git).prepare(LocalSource(root),'a'*32)
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    entered,release=Event(),Event()
    with EnvironmentOperations(env,'S',tmp_path/'state',policy) as operations:
        send(operations,snapshot,tmp_path/'input')
        normal=operations.inputs.install
        def delayed(payload,control):
            entered.set();assert release.wait(10)
            return normal(payload,control)
        monkeypatch.setattr(operations.inputs,'install',delayed)
        payload=dict(input_id='a'*32,original_head=snapshot.original_head,input_snapshot=snapshot.input_snapshot)
        operations.submit('b'*32,'git_input',payload,{})
        try:
            assert entered.wait(5)
            with pytest.raises(GitInputError,match='git_input_install_unconfirmed'):
                request(operations,'resource_info',{'path':str(code/'app.txt')})
            with pytest.raises(GitInputError,match='git_input_install_unconfirmed'):
                operations.submit('c'*32,'shell',{}, {})
            assert request(operations,'hello',{})['session_id']=='S'
        finally:release.set()
        assert operations.wait('b'*32,10)
        assert operations.journal.get('b'*32)['state']=='completed'


def test_failed_partial_installation_stays_blocked_after_envd_restarts(tmp_path,monkeypatch):
    from naiyue_test_agent.contracts.projects import LocalSource
    root=repository(tmp_path);policy=SandboxSettings()
    snapshot=SnapshotBuilder(tmp_path/'input',policy.git).prepare(LocalSource(root),'a'*32)
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',policy) as operations:
        send(operations,snapshot,tmp_path/'input')
        def fail(payload,control):
            (code/'partial').write_text('incomplete')
            raise OSError('disk failure')
        monkeypatch.setattr(operations.inputs,'install',fail)
        payload=dict(input_id='a'*32,original_head=snapshot.original_head,input_snapshot=snapshot.input_snapshot)
        operations.submit('b'*32,'git_input',payload,{})
        assert operations.wait('b'*32,10)
        assert operations.journal.get('b'*32)['state']=='unknown'
    with EnvironmentOperations(env,'S',tmp_path/'state',policy) as recovered:
        assert recovered.submit('b'*32,'git_input',payload,{})['state']=='unknown'
        with pytest.raises(GitInputError,match='git_input_install_unconfirmed'):
            request(recovered,'resource_info',{'path':str(code/'partial')})
        assert (code/'partial').read_text()=='incomplete'
