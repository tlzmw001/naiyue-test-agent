"""Adversarial result objects, finite capture admission and diff representation."""
from dataclasses import asdict
from threading import Event
import os

import pytest

from git_input_helpers import git
from test_git_results import delivery,capture
from test_envd_git_inputs import request
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.execution.git_results import ResultImporter


def expected(snapshot):return dict(binding_id='B',project_id='P',session_id='S',**asdict(snapshot))


def test_unexpected_bundle_ref_is_not_imported(delivery,tmp_path):
    _,code,snapshot,operations,policy,_=delivery
    (code/'app.txt').write_text('agent\n');receipt,_=capture(delivery)
    path=operations.results.bundle_path('c'*32);data=path.read_bytes().replace(
        b'refs/naiyue/tasks/'+b'c'*32,b'refs/heads/attack/'+b'c'*32,1)
    import hashlib
    bad=tmp_path/'bad.bundle';bad.write_bytes(data)
    receipt=dict(receipt,bundle_size=len(data),bundle_sha256=hashlib.sha256(data).hexdigest())
    with pytest.raises(GitInputError,match='git_result_invalid_bundle'):
        ResultImporter(tmp_path/'received',policy.git).verify(bad,receipt,expected(snapshot))


def test_rename_binary_deletion_mode_and_external_diff_are_reviewable(delivery,tmp_path):
    root,code,snapshot,operations,policy,_=delivery
    (code/'renamed.txt').write_bytes((code/'app.txt').read_bytes());(code/'app.txt').unlink()
    (code/'binary.bin').write_bytes(b'\x00\x01binary');(code/'executable.sh').write_text('echo fixture\n')
    (code/'executable.sh').chmod(0o755)
    receipt,_=capture(delivery,include=('renamed.txt','binary.bin','executable.sh'))
    importer=ResultImporter(tmp_path/'received',policy.git)
    result=importer.verify(operations.results.bundle_path('c'*32),receipt,expected(snapshot))
    paths={row['path']:row for row in result['files']}
    assert paths['renamed.txt']['status']=='R' and paths['renamed.txt']['old_path']=='app.txt'
    assert paths['executable.sh']['new_mode']=='100755'
    assert importer.diff(receipt,'binary.bin')['binary'] is True
    assert importer.diff(receipt,'binary.bin')['patch'] is None
    marker=tmp_path/'executed';script=tmp_path/'evil.sh';script.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\n');script.chmod(0o755)
    git(importer.repository.path,'config','diff.external',str(script))
    git(root,'config','core.hooksPath',str(tmp_path))
    hook=tmp_path/'reference-transaction';hook.write_bytes(script.read_bytes());hook.chmod(0o755)
    assert 'rename from app.txt' in importer.diff(receipt,'renamed.txt')['patch']
    importer.publish(receipt,__import__('naiyue_test_agent.contracts.projects',fromlist=['LocalSource']).LocalSource(root))
    assert not marker.exists()


@pytest.mark.parametrize('path,content,code',[
    ('.env',b'fixture','git_input_ignored'),
    ('private.pem',b'-----BEGIN PRIVATE KEY-----\nfixture','git_input_sensitive')])
def test_current_credentials_and_ignored_files_are_refused(delivery,path,content,code):
    _,sandbox,snapshot,operations,_,_=delivery
    (sandbox/path).write_bytes(content)
    payload=dict(result_id='c'*32,input_id=snapshot.input_id,original_head=snapshot.original_head,
        input_snapshot=snapshot.input_snapshot,include_untracked=[path])
    operations.submit('c'*32,'git_result',payload,{})
    assert operations.wait('c'*32,10)
    assert operations.journal.get('c'*32)['result']['error']['code']==code


def test_capture_reservation_blocks_new_execution_and_reads(delivery,monkeypatch):
    _,_,snapshot,operations,_,_=delivery
    entered,release=Event(),Event();normal=operations.results.capture
    def delayed(*args):
        entered.set();assert release.wait(10);return normal(*args)
    monkeypatch.setattr(operations.results,'capture',delayed)
    payload=dict(result_id='c'*32,input_id=snapshot.input_id,original_head=snapshot.original_head,
        input_snapshot=snapshot.input_snapshot,include_untracked=[])
    operations.submit('c'*32,'git_result',payload,{})
    try:
        assert entered.wait(5)
        with pytest.raises(GitInputError,match='git_result_busy'):operations.submit('d'*32,'shell',{}, {})
        with pytest.raises(GitInputError,match='git_result_busy'):request(operations,'result_options',{})
        assert request(operations,'hello',{})['session_id']=='S'
    finally:release.set()
    assert operations.wait('c'*32,10)


def test_unknown_prior_process_prevents_capture(delivery):
    _,_,snapshot,operations,_,_=delivery
    operations.journal.reserve('d'*32,'shell',{},{});operations.journal.finish('d'*32,'unknown')
    payload=dict(result_id='c'*32,input_id=snapshot.input_id,original_head=snapshot.original_head,
        input_snapshot=snapshot.input_snapshot,include_untracked=[])
    with pytest.raises(GitInputError,match='git_result_busy'):operations.submit('c'*32,'git_result',payload,{})


def test_publish_ref_never_dereferences_symbolic_branch(delivery,tmp_path):
    from naiyue_test_agent.contracts.projects import LocalSource
    root,code,snapshot,operations,policy,_=delivery
    (code/'app.txt').write_text('agent\n');receipt,_=capture(delivery)
    importer=ResultImporter(tmp_path/'received',policy.git)
    importer.verify(operations.results.bundle_path('c'*32),receipt,expected(snapshot))
    git(root,'symbolic-ref','refs/naiyue/tasks/'+'c'*32,'refs/heads/user-new-branch')
    with pytest.raises(GitInputError,match='git_result_ref_conflict'):importer.publish(receipt,LocalSource(root))
    assert git(root,'for-each-ref','refs/heads/user-new-branch')==b''


@pytest.mark.parametrize('attack',['url_rewrite','alternate_command'])
def test_local_import_does_not_execute_repository_commands(delivery,tmp_path,attack):
    from naiyue_test_agent.contracts.projects import LocalSource
    root,code,snapshot,operations,policy,_=delivery
    (code/'app.txt').write_text('agent\n');receipt,_=capture(delivery)
    importer=ResultImporter(tmp_path/'received',policy.git)
    importer.verify(operations.results.bundle_path('c'*32),receipt,expected(snapshot))
    marker=tmp_path/'host-command';script=tmp_path/'host-command.sh'
    script.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n');script.chmod(0o755)
    if attack=='url_rewrite':
        git(root,'config','url.ssh://fixture.invalid/rewrite.insteadOf',str(importer.repository.path))
        git(root,'config','core.sshCommand',str(script))
    else:
        alternate=tmp_path/'alternate.git';alternate.mkdir();git(alternate,'init','--bare','-q')
        (root/'.git'/'objects'/'info'/'alternates').write_text(str(alternate/'objects')+'\n')
        git(root,'config','core.alternateRefsCommand',str(script))
    try:importer.publish(receipt,LocalSource(root))
    except GitInputError:pass
    assert not marker.exists()
