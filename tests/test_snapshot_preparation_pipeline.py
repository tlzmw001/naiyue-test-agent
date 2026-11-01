"""Snapshot acceleration preserves actual objects and both source checks."""
import hashlib
from threading import Event
import pytest

from git_input_helpers import git,repository
from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.execution.git_repository import GitRepository
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.sandbox_settings import GitInputSettings


@pytest.mark.parametrize('dirty',[False,True])
def test_snapshot_uses_at_most_twenty_git_calls_with_one_commit_stream(tmp_path,monkeypatch,dirty):
    root=repository(tmp_path)
    if dirty:(root/'app.txt').write_bytes(b'current\n')
    seen=[];original=GitRepository.run
    def run(self,args,**kwargs):
        seen.append(tuple(args));return original(self,args,**kwargs)
    monkeypatch.setattr(GitRepository,'run',run)
    snapshot=SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert len(seen)<=20,seen
    assert sum(args[0]=='config' for args in seen)==2
    assert sum(args[0]=='fast-import' for args in seen)==1
    assert not any(args[0] in ('write-tree','update-index','commit-tree') for args in seen)
    assert (snapshot.input_snapshot==snapshot.original_head) is (not dirty)
    assert git(tmp_path/'snapshot/objects.git','show',snapshot.input_snapshot+':app.txt')==(b'current\n' if dirty else b'base\n')
    assert snapshot.tree==git(tmp_path/'snapshot/objects.git','rev-parse',snapshot.input_snapshot+'^{tree}').decode().strip()
    if dirty:
        assert git(tmp_path/'snapshot/objects.git','rev-parse',snapshot.input_snapshot+'^').decode().strip()==snapshot.original_head
    assert git(tmp_path/'snapshot/objects.git','for-each-ref','--format=%(refname)','refs/naiyue/pending/')==b''


def test_stream_quotes_paths_and_keeps_binary_content_and_deletions(tmp_path):
    root=repository(tmp_path);(root/'app.txt').unlink()
    path='目录/space "quote".bin';target=root/path;target.parent.mkdir();target.write_bytes(b'\0\xff\nM 100644 :1 injected\n')
    snapshot=SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32,include_untracked=(path,))
    assert {item.path for item in snapshot.manifest}=={'.gitignore',path}
    assert git(tmp_path/'snapshot/objects.git','show',snapshot.input_snapshot+':'+path)==target.read_bytes()
    assert hashlib.sha256((tmp_path/'snapshot/input.bundle').read_bytes()).hexdigest()==snapshot.bundle_sha256


def test_empty_working_tree_can_be_imported(tmp_path):
    root=repository(tmp_path);(root/'app.txt').unlink();(root/'.gitignore').unlink()
    snapshot=SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert snapshot.manifest==() and snapshot.total_bytes==0
    assert git(tmp_path/'snapshot/objects.git','ls-tree','-r',snapshot.input_snapshot)==b''


@pytest.mark.parametrize('key',['core.filemode','core.symlinks'])
def test_combined_bool_query_keeps_defaults_duplicate_values_and_invalid_rejection(tmp_path,key):
    root=repository(tmp_path)
    git(root,'config','--add',key,'false');git(root,'config','--add',key,'true')
    snapshot=SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert snapshot.input_snapshot==snapshot.original_head
    git(root,'config','--add',key,'INVALID')
    with pytest.raises(GitInputError,match='git_input_git_failed'):
        SnapshotBuilder(tmp_path/'invalid',GitInputSettings()).prepare(LocalSource(root),'b'*32)


def test_valid_looking_forged_stream_commit_and_tree_cannot_replace_captured_bytes(tmp_path,monkeypatch):
    root=repository(tmp_path);head=git(root,'rev-parse','HEAD').strip()
    tree=git(root,'rev-parse','HEAD^{tree}').strip();(root/'app.txt').write_bytes(b'changed\n')
    original=GitRepository.run
    def run(self,args,**kwargs):
        result=original(self,args,**kwargs)
        if args[0]=='fast-import':
            rows=result.splitlines()
            rows[-2]=head;rows[-1]=b'040000 tree '+tree+b'\t'
            return b'\n'.join(rows)+b'\n'
        return result
    monkeypatch.setattr(GitRepository,'run',run)
    with pytest.raises(GitInputError,match='git_input_invalid_object'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert not (tmp_path/'snapshot/input.bundle').exists()


@pytest.mark.parametrize('pair',['fetch_read','verify_objects'])
def test_independent_snapshot_steps_overlap_with_real_objects(tmp_path,monkeypatch,pair):
    from naiyue_test_agent.execution import git_snapshot
    root=repository(tmp_path);first,second=Event(),Event()
    targets=([(SnapshotBuilder,'_fetch'),(SnapshotBuilder,'_read_local')] if pair=='fetch_read'
        else [(SnapshotBuilder,'_verify_local'),(git_snapshot,'validate_tree')])
    for (target,name),mine,other in zip(targets,[first,second],[second,first],strict=True):
        original=getattr(target,name)
        def wrapped(*args,_original=original,_mine=mine,_other=other,**kwargs):
            _mine.set();assert _other.wait(5);return _original(*args,**kwargs)
        monkeypatch.setattr(target,name,wrapped)
    snapshot=SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert first.is_set() and second.is_set() and snapshot.input_snapshot==snapshot.original_head


def test_joined_workers_settle_every_branch_and_keep_primary_error(caplog):
    from naiyue_test_agent.execution.git_snapshot import _joined
    first=GitInputError('git_input_sensitive');second=OSError('PRIVATE_SECONDARY');settled=Event()
    def fail():raise first
    def secondary():
        try:raise second
        finally:settled.set()
    with pytest.raises(GitInputError) as caught:_joined([fail,secondary])
    assert caught.value is first and settled.is_set()
    assert 'snapshot_parallel_secondary_failed' in caplog.text and 'PRIVATE_SECONDARY' not in caplog.text


def test_forged_tree_metadata_is_checked_against_the_actual_commit(tmp_path,monkeypatch):
    root=repository(tmp_path);(root/'app.txt').write_bytes(b'changed\n');original=GitRepository.run
    def run(self,args,**kwargs):
        result=original(self,args,**kwargs)
        if args[0]=='fast-import':
            rows=result.splitlines();rows[-1]=b'040000 tree '+b'0'*40+b'\t'
            return b'\n'.join(rows)+b'\n'
        return result
    monkeypatch.setattr(GitRepository,'run',run)
    with pytest.raises(GitInputError,match='git_input_invalid_object'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert not (tmp_path/'snapshot/input.bundle').exists()
