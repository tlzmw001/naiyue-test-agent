"""User working-copy inputs are explicit; application Git never mutates its index."""
import hashlib
import os
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.projects import LocalSource, GitSource
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.execution import git_snapshot
from naiyue_test_agent.execution.git_repository import GitRepository
from naiyue_test_agent.sandbox_settings import GitInputSettings
from git_input_helpers import git, repository


def builder(tmp_path, **settings):
    return SnapshotBuilder(tmp_path/'snapshot', GitInputSettings(**settings))


def blob(tmp_path, path):
    return git(tmp_path/'snapshot'/'objects.git','show','refs/naiyue/input/'+'a'*32+':'+path)


def test_windows_descriptor_ctime_difference_does_not_reject_unchanged_input(tmp_path, monkeypatch):
    root=repository(tmp_path)
    info=(root/'app.txt').stat()
    identity=(info.st_dev,info.st_ino)
    fstat=os.fstat
    def descriptor_stat(fd):
        value=fstat(fd)
        if (value.st_dev,value.st_ino)!=identity:
            return value
        fields={name:getattr(value,name) for name in dir(value) if name.startswith('st_')}
        fields['st_ctime_ns']+=1_000_000_000
        return SimpleNamespace(**fields)
    monkeypatch.setattr(git_snapshot.os,'fstat',descriptor_stat)
    result=builder(tmp_path).prepare(LocalSource(root),'a'*32)
    assert blob(tmp_path,'app.txt')==b'base\n'
    assert any(item.path=='app.txt' for item in result.manifest)


def test_partial_staging_and_user_git_objects_remain_unchanged(tmp_path):
    root=repository(tmp_path)
    (root/'app.txt').write_text('staged\n');git(root,'add','app.txt')
    (root/'app.txt').write_text('current\n')
    (root/'binary.bin').write_bytes(b'\x00\xff\x10');git(root,'add','binary.bin')
    (root/'run.sh').write_text('echo fixture\n');(root/'run.sh').chmod(0o755);git(root,'add','run.sh')
    (root/'new.txt').write_text('excluded\n')
    before_index=(root/'.git/index').read_bytes();head=git(root,'rev-parse','HEAD')
    objects=sorted(str(p.relative_to(root/'.git/objects')) for p in (root/'.git/objects').rglob('*') if p.is_file())
    snapshot=builder(tmp_path).prepare(LocalSource(root), 'a'*32)
    assert snapshot.original_head==head.decode().strip()
    assert snapshot.input_snapshot!=snapshot.original_head
    assert blob(tmp_path,'app.txt')==b'current\n'
    assert blob(tmp_path,'binary.bin')==b'\x00\xff\x10'
    assert {item.path:item.mode for item in snapshot.manifest}['run.sh']=='100755'
    assert 'new.txt' not in {item.path for item in snapshot.manifest}
    assert any(item.path=='new.txt' and item.reason=='untracked' for item in snapshot.excluded)
    assert (root/'.git/index').read_bytes()==before_index
    assert git(root,'show',':app.txt')==b'staged\n'
    assert git(root,'rev-parse','HEAD')==head and (root/'app.txt').read_text()=='current\n'
    assert sorted(str(p.relative_to(root/'.git/objects')) for p in (root/'.git/objects').rglob('*') if p.is_file())==objects
    assert hashlib.sha256((tmp_path/'snapshot/input.bundle').read_bytes()).hexdigest()==snapshot.bundle_sha256


def test_snapshot_uses_bounded_batch_git_commands(tmp_path, monkeypatch):
    root=repository(tmp_path)
    for number in range(5):
        (root/f'extra-{number}.txt').write_text(f'content-{number}\n')
    git(root,'add','.')
    seen=[];original=GitRepository.run
    def run(self,args,**kwargs):
        seen.append(tuple(args))
        return original(self,args,**kwargs)
    monkeypatch.setattr(GitRepository,'run',run)
    result=builder(tmp_path).prepare(LocalSource(root),'a'*32)
    assert len(result.manifest)==7
    assert sum(args[0]=='fast-import' for args in seen)==1
    assert sum(args[:2]==('cat-file','--batch-check') for args in seen)==1
    assert sum(args[:2]==('cat-file','--batch') for args in seen)==1
    assert not any(args[0]=='hash-object' or args[:2]==('cat-file','-s') or
        args[:2]==('cat-file','blob') for args in seen)


def test_batch_validation_splits_large_output_without_changing_manifest(tmp_path,monkeypatch):
    root=repository(tmp_path)
    for number in range(3):
        (root/f'extra-{number}.txt').write_bytes(bytes([65+number])*800)
    git(root,'add','.')
    seen=[];original=GitRepository.run
    def run(self,args,**kwargs):
        if args[:2]==['cat-file','--batch']:
            seen.append(kwargs['output_limit'])
        return original(self,args,**kwargs)
    monkeypatch.setattr(GitRepository,'run',run)
    snapshot=builder(tmp_path,max_command_bytes=1024).prepare(LocalSource(root),'a'*32)
    assert len(snapshot.manifest)==5 and len(seen)>=3
    assert all(size<=1700 for size in seen)


def test_explicit_untracked_hidden_skill_and_tracked_delete(tmp_path):
    root=repository(tmp_path)
    skill=root/'.agents/skills/http/SKILL.md';skill.parent.mkdir(parents=True);skill.write_text('fixture skill\n')
    (root/'app.txt').unlink()
    snapshot=builder(tmp_path).prepare(LocalSource(root),'a'*32,include_untracked=('.agents/skills/http/SKILL.md',))
    assert 'app.txt' not in {item.path for item in snapshot.manifest}
    assert blob(tmp_path,'.agents/skills/http/SKILL.md')==b'fixture skill\n'
    assert git(root,'diff','--cached')==b''


@pytest.mark.parametrize('name,content',[('.env','FIXTURE=not-a-key\n'),('id_ed25519','synthetic\n'),
    ('file.pem','-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----\n')])
def test_selected_credentials_fail_without_body_in_error(tmp_path,name,content):
    root=repository(tmp_path);(root/name).write_text(content)
    git(root,'add','--force','--',name)
    with pytest.raises(GitInputError) as caught:builder(tmp_path).prepare(LocalSource(root),'a'*32)
    assert caught.value.code=='git_input_sensitive'
    assert caught.value.paths==(name,) and 'synthetic' not in str(caught.value)
    assert 'FIXTURE=' not in str(caught.value)


@pytest.mark.parametrize('name,content',[('.env.example','FIXTURE=example\n'),
    ('certificate.pem','-----BEGIN CERTIFICATE-----\nsynthetic\n-----END CERTIFICATE-----\n'),
    ('id_ed25519.pub','ssh-ed25519 synthetic\n')])
def test_examples_and_public_certificates_are_not_private_keys(tmp_path,name,content):
    root=repository(tmp_path);(root/name).write_text(content);git(root,'add','--',name)
    assert name in {item.path for item in builder(tmp_path).prepare(LocalSource(root),'a'*32).manifest}


@pytest.mark.parametrize('selected,code',[('.env','git_input_ignored'),('../outside','git_input_invalid_path'),
    ('/tmp/outside','git_input_invalid_path'),('.git/config','git_input_invalid_path')])
def test_explicit_selection_cannot_bypass_ignore_or_project_boundary(tmp_path,selected,code):
    root=repository(tmp_path);(root/'.env').write_text('synthetic\n')
    with pytest.raises(GitInputError,match=code):builder(tmp_path).prepare(LocalSource(root),'a'*32,include_untracked=(selected,))


def test_external_symlink_is_rejected_and_internal_relative_link_keeps_mode(tmp_path):
    root=repository(tmp_path);(tmp_path/'outside').write_text('outside fixture')
    (root/'link').symlink_to('../outside');git(root,'add','link')
    with pytest.raises(GitInputError,match='git_input_outside'):builder(tmp_path).prepare(LocalSource(root),'a'*32)
    (root/'link').unlink();(root/'link').symlink_to('app.txt')
    result=SnapshotBuilder(tmp_path/'safe-snapshot',GitInputSettings()).prepare(LocalSource(root),'b'*32)
    assert {item.path:item.mode for item in result.manifest}['link']=='120000'


@pytest.mark.parametrize('scope',['local','global'])
@pytest.mark.parametrize('enabled',[False,True])
def test_snapshot_respects_effective_git_filemode_and_preserves_index(tmp_path,monkeypatch,scope,enabled):
    root=repository(tmp_path)
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('HOME',str(home))
    target=root/'run.sh';target.write_text('echo fixture\n');target.chmod(0o644)
    git(root,'add','run.sh');git(root,'update-index','--chmod=+x','run.sh');git(root,'commit','-qm','script')
    if scope=='global':git(root,'config','--unset','core.filemode')
    else:git(root,'config','--global','core.filemode',str(not enabled).lower())
    git(root,'config','--'+scope,'core.filemode',str(enabled).lower())
    index=(root/'.git/index').read_bytes();head=git(root,'rev-parse','HEAD')
    snapshot=builder(tmp_path).prepare(LocalSource(root),'a'*32)
    expected='100644' if enabled else '100755'
    assert {item.path:item.mode for item in snapshot.manifest}['run.sh']==expected
    assert (snapshot.input_snapshot==snapshot.original_head) is (not enabled)
    assert (root/'.git/index').read_bytes()==index and git(root,'rev-parse','HEAD')==head


def test_filemode_false_does_not_make_tracked_or_new_files_executable(tmp_path):
    root=repository(tmp_path);git(root,'config','core.filemode','false')
    (root/'app.txt').chmod(0o755)
    (root/'new.sh').write_text('echo new\n');(root/'new.sh').chmod(0o755)
    snapshot=builder(tmp_path).prepare(LocalSource(root),'a'*32,include_untracked=('new.sh',))
    modes={item.path:item.mode for item in snapshot.manifest}
    assert modes['app.txt']==modes['new.sh']=='100644'


@pytest.mark.parametrize('scope',['local','global'])
@pytest.mark.parametrize('enabled',[False,True])
def test_snapshot_respects_git_symlinks_for_plain_file_checkout(tmp_path,monkeypatch,scope,enabled):
    root=repository(tmp_path)
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('HOME',str(home))
    if scope=='global':
        git(root,'config','--local','core.symlinks','true');git(root,'config','--unset','core.symlinks')
    else:git(root,'config','--global','core.symlinks',str(not enabled).lower())
    git(root,'config','--'+scope,'core.symlinks',str(enabled).lower())
    (root/'link').write_bytes(b'app.txt')
    oid=GitRepository(root,GitInputSettings()).run(['hash-object','-w','--stdin'],data=b'app.txt').decode().strip()
    git(root,'update-index','--add','--cacheinfo','120000',oid,'link');git(root,'commit','-qm','link')
    index=(root/'.git/index').read_bytes();head=git(root,'rev-parse','HEAD')
    snapshot=builder(tmp_path).prepare(LocalSource(root),'a'*32)
    assert {item.path:item.mode for item in snapshot.manifest}['link']==('100644' if enabled else '120000')
    assert (snapshot.input_snapshot==snapshot.original_head) is (not enabled)
    assert blob(tmp_path,'link')==b'app.txt'
    assert (root/'.git/index').read_bytes()==index and git(root,'rev-parse','HEAD')==head


@pytest.mark.parametrize('target',['../outside','A/../outside'])
def test_plain_file_symlink_checkout_cannot_bypass_link_safety(tmp_path,target):
    root=repository(tmp_path);git(root,'config','core.symlinks','false')
    repo=GitRepository(root,GitInputSettings())
    for name,content in [('a',b'.'),('link',target.encode())]:
        (root/name).write_bytes(content)
        oid=repo.run(['hash-object','-w','--stdin'],data=content).decode().strip()
        git(root,'update-index','--add','--cacheinfo','120000',oid,name)
    with pytest.raises(GitInputError,match='git_input_outside'):
        builder(tmp_path).prepare(LocalSource(root),'a'*32)


@pytest.mark.parametrize('feature',['lfs','filter','submodule'])
def test_unsupported_git_features_fail_before_any_hook_or_filter_executes(tmp_path,feature):
    root=repository(tmp_path)
    marker=tmp_path/'unexpected-execution'
    if feature=='submodule':
        git(root,'update-index','--add','--cacheinfo','160000',git(root,'rev-parse','HEAD').decode().strip(),'module')
    else:
        (root/'.gitattributes').write_text('app.txt filter='+('lfs' if feature=='lfs' else 'test')+'\n')
        git(root,'add','.gitattributes')
    git(root,'config','filter.test.clean','touch '+str(marker));git(root,'config','filter.test.smudge','touch '+str(marker))
    with pytest.raises(GitInputError,match='git_input_unsupported'):builder(tmp_path).prepare(LocalSource(root),'a'*32)
    assert not marker.exists()


def test_budget_failure_preserves_user_index(tmp_path):
    root=repository(tmp_path);before=(root/'.git/index').read_bytes()
    with pytest.raises(GitInputError,match='git_input_budget'):builder(tmp_path,max_file_bytes=2).prepare(LocalSource(root),'a'*32)
    assert (root/'.git/index').read_bytes()==before


def test_git_source_fetches_fixed_head_without_local_working_copy(tmp_path):
    root=repository(tmp_path)
    snapshot=builder(tmp_path).prepare(GitSource(root.as_uri(),'HEAD'),'a'*32)
    assert snapshot.input_snapshot==snapshot.original_head
    assert blob(tmp_path,'app.txt')==b'base\n'
    assert b'Input Test' not in (tmp_path/'snapshot/objects.git/config').read_bytes()
    with pytest.raises(GitInputError,match='git_input_invalid_selection'):
        SnapshotBuilder(tmp_path/'another',GitInputSettings()).prepare(GitSource(root.as_uri(),'HEAD'),'b'*32,include_untracked=('new.txt',))


@pytest.mark.parametrize('commit',[False,True])
def test_no_git_and_unborn_head_are_explicit_failures(tmp_path,commit):
    root=tmp_path/'project';root.mkdir()
    if commit:git(root,'init','-q')
    with pytest.raises(GitInputError,match='git_input_no_head' if commit else 'git_input_no_repository'):
        builder(tmp_path).prepare(LocalSource(root),'a'*32)
