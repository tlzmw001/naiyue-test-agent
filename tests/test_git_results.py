"""Result delivery uses real Git objects and preserves both working copies."""
from dataclasses import asdict
from pathlib import Path
import base64
import hashlib

import pytest

from git_input_helpers import git, repository
from test_envd_git_inputs import send
from naiyue_test_agent.contracts.environment import EnvironmentRef
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.envd.operations import EnvironmentOperations
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.sandbox_settings import SandboxSettings


@pytest.fixture
def delivery(tmp_path):
    policy=SandboxSettings();root=repository(tmp_path)
    (root/'app.txt').write_text('user staged\n');git(root,'add','app.txt')
    (root/'app.txt').write_text('user working\n')
    before=(git(root,'rev-parse','HEAD'),git(root,'ls-files','--stage','-z'),(root/'app.txt').read_bytes())
    snapshot=SnapshotBuilder(tmp_path/'input',policy.git).prepare(LocalSource(root),'a'*32)
    code=tmp_path/'code';code.mkdir();env=EnvironmentRef('B','sandbox','P',str(code),'linux')
    with EnvironmentOperations(env,'S',tmp_path/'state',policy) as operations:
        send(operations,snapshot,tmp_path/'input')
        operations.submit('b'*32,'git_input',dict(input_id='a'*32,original_head=snapshot.original_head,
            input_snapshot=snapshot.input_snapshot),{})
        assert operations.wait('b'*32,10)
        yield root,code,snapshot,operations,policy,before


def capture(delivery,ident='c'*32,include=()):
    root,code,snapshot,operations,policy,before=delivery
    payload=dict(result_id=ident,input_id=snapshot.input_id,original_head=snapshot.original_head,
        input_snapshot=snapshot.input_snapshot,include_untracked=list(include))
    operations.submit(ident,'git_result',payload,{})
    assert operations.wait(ident,10)
    row=operations.journal.get(ident)
    assert row['state']=='completed',row
    return row['result']['value'],payload


def test_result_snapshot_diff_uses_input_and_preserves_indexes(delivery,tmp_path):
    from naiyue_test_agent.execution.git_results import ResultImporter
    root,code,snapshot,operations,policy,before=delivery
    git(code,'add','app.txt');index=git(code,'ls-files','--stage','-z');head=git(code,'rev-parse','HEAD')
    (code/'new.txt').write_text('agent new\n')
    receipt,payload=capture(delivery,include=('new.txt',))
    source=operations.results.bundle_path(receipt['result_id'])
    imported=ResultImporter(tmp_path/'received',policy.git).verify(source,receipt,
        dict(binding_id='B',project_id='P',session_id='S',**asdict(snapshot)))
    assert [row['path'] for row in imported['files']]==['new.txt']
    importer=ResultImporter(tmp_path/'received',policy.git)
    assert '+agent new' in importer.diff(receipt,'new.txt')['patch']
    importer.publish(receipt,LocalSource(root))
    assert git(root,'rev-parse','refs/naiyue/tasks/'+'c'*32).decode().strip()==receipt['output_commit']
    assert (git(root,'rev-parse','HEAD'),git(root,'ls-files','--stage','-z'),(root/'app.txt').read_bytes())==before
    assert git(code,'ls-files','--stage','-z')==index and git(code,'rev-parse','HEAD')==head
    (code/'new.txt').write_text('later\n')
    assert operations.submit('c'*32,'git_result',payload,{})['result']['value']==receipt


def test_result_captures_tracked_changes_and_lists_excluded_new_files(delivery,tmp_path):
    from naiyue_test_agent.execution.git_results import ResultImporter
    root,code,snapshot,operations,policy,before=delivery
    (code/'app.txt').write_text('agent changed\n');(code/'extra.txt').write_text('not selected')
    receipt,_=capture(delivery)
    result=ResultImporter(tmp_path/'received',policy.git).verify(operations.results.bundle_path('c'*32),receipt,
        dict(binding_id='B',project_id='P',session_id='S',**asdict(snapshot)))
    assert [f['path'] for f in result['files']]==['app.txt']
    assert {'path':'extra.txt','reason':'untracked'} in receipt['excluded']


def test_no_change_has_empty_diff(delivery,tmp_path):
    from naiyue_test_agent.execution.git_results import ResultImporter
    _,_,snapshot,operations,policy,_=delivery
    receipt,_=capture(delivery)
    assert receipt['output_commit']==snapshot.input_snapshot
    result=ResultImporter(tmp_path/'received',policy.git).verify(operations.results.bundle_path('c'*32),receipt,
        dict(binding_id='B',project_id='P',session_id='S',**asdict(snapshot)))
    assert result['files']==[]


@pytest.mark.parametrize('field,value',[('binding_id','another'),('input_snapshot','e'*40),
    ('output_commit','f'*40),('bundle_sha256','0'*64)])
def test_tampered_receipt_never_publishes(delivery,tmp_path,field,value):
    from naiyue_test_agent.execution.git_results import ResultImporter
    root,code,snapshot,operations,policy,_=delivery
    (code/'app.txt').write_text('changed\n');receipt,_=capture(delivery)
    with pytest.raises(GitInputError):
        ResultImporter(tmp_path/'received',policy.git).verify(operations.results.bundle_path('c'*32),
            dict(receipt,**{field:value}),dict(binding_id='B',project_id='P',session_id='S',**asdict(snapshot)))
    assert git(root,'for-each-ref','refs/naiyue/tasks/')==b''


def test_export_rejects_non_descendant_head(delivery):
    _,code,snapshot,operations,_,_=delivery
    git(code,'checkout','--orphan','unrelated');git(code,'commit','-qm','unrelated')
    payload=dict(result_id='c'*32,input_id=snapshot.input_id,original_head=snapshot.original_head,
        input_snapshot=snapshot.input_snapshot,include_untracked=[])
    operations.submit('c'*32,'git_result',payload,{})
    assert operations.wait('c'*32,10)
    row=operations.journal.get('c'*32)
    assert row['state']=='failed' and row['result']['error']['code']=='git_result_not_descendant'
