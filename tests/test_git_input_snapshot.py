"""User working-copy inputs are explicit; application Git never mutates its index."""
import hashlib

import pytest

from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.contracts.projects import LocalSource, GitSource
from naiyue_test_agent.execution.git_snapshot import SnapshotBuilder
from naiyue_test_agent.sandbox_settings import GitInputSettings
from git_input_helpers import git, repository


def builder(tmp_path, **settings):
    return SnapshotBuilder(tmp_path/'snapshot', GitInputSettings(**settings))


def blob(tmp_path, path):
    return git(tmp_path/'snapshot'/'objects.git','show','refs/naiyue/input/'+'a'*32+':'+path)


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
