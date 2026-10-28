"""Reject malformed inputs and observable source races before accepting a snapshot."""
import os
import subprocess
import pytest

from naiyue_test_agent.contracts.git_inputs import GitInputError, input_path
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder, check_links, require_source_url
from naiyue_test_agent.execution.git_repository import GitRepository, check_cache_budget
from naiyue_test_agent.sandbox_settings import GitInputSettings
from git_input_helpers import git, repository


def test_dot_is_not_a_file_path():
    with pytest.raises(GitInputError,match='git_input_invalid_path'):
        input_path('.')


def test_invalid_link_encoding_is_an_explicit_input_failure():
    with pytest.raises(GitInputError,match='git_input_invalid_path'):
        check_links({'link':('120000',b'\xff')})


@pytest.mark.parametrize('name,alias', [('a','A'), ('é','e\u0301'), ('Dir/a','dir/a')])
def test_equivalent_link_spelling_cannot_hide_a_parent_escape(name,alias):
    with pytest.raises(GitInputError,match='git_input_outside'):
        check_links({name:('120000',b'.'), 'escape':('120000',(alias+'/../outside').encode())})


def test_link_directory_prefix_with_multiple_equivalent_spellings_is_rejected():
    with pytest.raises(GitInputError,match='git_input_outside'):
        check_links({'Dir/a':('120000',b'.'), 'dir/file':('100644',b'content')})


def test_precisely_spelled_internal_link_chain_is_accepted():
    check_links({'a':('120000',b'.'), 'b':('120000',b'a/inside'), 'inside':('100644',b'content')})


def test_malformed_url_is_an_explicit_source_failure():
    with pytest.raises(GitInputError,match='git_input_invalid_source'):
        require_source_url('ssh://[bad/ref','main')


def test_invalid_selection_elements_are_rejected_before_reading_source(tmp_path):
    with pytest.raises(GitInputError,match='git_input_invalid_selection'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(
            LocalSource(tmp_path),'a'*32,include_untracked=[[]])


def test_even_empty_cache_cannot_reserve_more_than_its_budget(tmp_path):
    with pytest.raises(GitInputError,match='git_input_budget'):
        check_cache_budget(tmp_path,-1)


def test_index_change_during_snapshot_is_rejected(tmp_path,monkeypatch):
    root=repository(tmp_path)
    original=GitRepository.run;changed=False
    def run(self,args,**kwargs):
        nonlocal changed
        result=original(self,args,**kwargs)
        if args[0]=='commit-tree' and not changed:
            changed=True
            git(root,'update-index','--chmod=+x','app.txt')
        return result
    (root/'app.txt').write_text('dirty\n')
    monkeypatch.setattr(GitRepository,'run',run)
    with pytest.raises(GitInputError,match='git_input_source_changed'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert changed


def test_same_size_file_change_during_snapshot_is_rejected(tmp_path,monkeypatch):
    root=repository(tmp_path)
    original=GitRepository.run;changed=False
    def run(self,args,**kwargs):
        nonlocal changed
        result=original(self,args,**kwargs)
        if args[0]=='fast-import' and not changed:
            changed=True
            target=root/'app.txt'
            target.write_bytes(b'evil\n')
            info=target.stat()
            os.utime(target,ns=(info.st_atime_ns,info.st_mtime_ns+1_000_000_000))
        return result
    monkeypatch.setattr(GitRepository,'run',run)
    with pytest.raises(GitInputError,match='git_input_source_changed'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert changed


@pytest.mark.parametrize('command', ['fast-import','--batch-check','--batch'])
def test_batch_git_output_mismatch_never_accepts_snapshot(tmp_path,monkeypatch,command):
    root=repository(tmp_path)
    original=GitRepository.run
    seen=False
    def run(self,args,**kwargs):
        nonlocal seen
        result=original(self,args,**kwargs)
        if args[0]==command or command.startswith('--') and tuple(args[:2])==('cat-file',command):
            seen=True
            return b''
        return result
    monkeypatch.setattr(GitRepository,'run',run)
    with pytest.raises(GitInputError,match='git_input_invalid_object'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert seen
    result=subprocess.run(['git','show-ref','--verify','refs/naiyue/input/'+'a'*32],
        cwd=tmp_path/'snapshot'/'objects.git',capture_output=True)
    assert result.returncode!=0


def test_fast_import_reordered_marks_are_rejected(tmp_path,monkeypatch):
    root=repository(tmp_path)
    original=GitRepository.run
    def run(self,args,**kwargs):
        result=original(self,args,**kwargs)
        if args[0]=='fast-import':
            return b'\n'.join(reversed(result.splitlines()))+b'\n'
        return result
    monkeypatch.setattr(GitRepository,'run',run)
    with pytest.raises(GitInputError,match='git_input_invalid_object'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)


@pytest.mark.parametrize('scope',['global','local'])
def test_explicit_selection_cannot_bypass_real_git_excludes(tmp_path,monkeypatch,scope):
    root=repository(tmp_path)
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('HOME',str(home))
    excludes=tmp_path/'ignore';excludes.write_text('private.json\n')
    git(root,'config','--'+scope,'core.excludesFile',str(excludes))
    (root/'private.json').write_text('private input\n')
    assert git(root,'check-ignore','private.json').strip()==b'private.json'
    with pytest.raises(GitInputError,match='git_input_ignored'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(
            LocalSource(root),'a'*32,include_untracked=('private.json',))


@pytest.mark.parametrize('code',[{},[],'git_input_corrupt'])
def test_malformed_git_error_reply_remains_protocol_failure(code):
    import json
    from naiyue_test_agent.contracts.sandbox import SandboxRecord,SandboxError
    from naiyue_test_agent.execution.docker_environment import DockerEnvironment
    row=SandboxRecord('B','P','S','running','D','container','volume','image',{},container_id='c'*64)
    class CLI:
        def run(self,args,*,input_bytes=None,**kwargs):
            request=json.loads(input_bytes)
            return json.dumps(dict(request,ok=False,error={'code':code,'message':None})).encode()
    engine=DockerEnvironment(CLI());engine.validate=lambda *_a,**_k:None
    row=row.changed(settings={'max_message_bytes':16777216})
    with pytest.raises(SandboxError,match='env_protocol_mismatch'):
        engine.request(row,'input_probe',{})


def test_non_utf8_index_path_has_a_classified_error(tmp_path):
    import subprocess
    root=repository(tmp_path)
    oid=git(root,'rev-parse','HEAD:app.txt').strip()
    subprocess.run(['git','update-index','-z','--index-info'],cwd=root,
        input=b'100644 '+oid+b'\tnon_utf8_\xff\0',check=True)
    with pytest.raises(GitInputError,match='git_input_invalid_path'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)


@pytest.mark.parametrize('key', ['core.filemode','core.symlinks'])
def test_source_mode_configuration_change_during_snapshot_is_rejected(tmp_path,monkeypatch,key):
    root=repository(tmp_path);git(root,'config',key,'true')
    original=GitRepository.run;changed=False
    def run(self,args,**kwargs):
        nonlocal changed
        result=original(self,args,**kwargs)
        if args[0]=='fast-import' and not changed:
            changed=True;git(root,'config',key,'false')
        return result
    monkeypatch.setattr(GitRepository,'run',run)
    with pytest.raises(GitInputError,match='git_input_source_changed'):
        SnapshotBuilder(tmp_path/'snapshot',GitInputSettings()).prepare(LocalSource(root),'a'*32)
    assert changed
