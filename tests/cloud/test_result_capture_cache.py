"""Immutable I reuse never replaces content checks of the writable workspace."""
import hashlib
import json
import os
import shutil
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from naiyue_test_agent.cloud_files import archive
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
from naiyue_test_agent.contracts.cloud_inputs import InputLimits,InputPackage
from naiyue_test_agent.contracts.cloud_results import ResultBoundary,ResultLimits,ResultPlan
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def setup_capture(tmp_path):
    source=tmp_path/'source';source.mkdir()
    for name,data in [('keep.txt',b'keep\r\n'),('change.bin',bytes(range(256))*32),('mode.sh',b'echo ok\n')]:
        (source/name).write_bytes(data)
    inputs=tmp_path/'input';limits=InputLimits()
    captured=capture(source,{'paths':['keep.txt','change.bin','mode.sh']},inputs,limits)
    workspace=tmp_path/'workspace';shutil.copytree(source,workspace)
    plan=ResultPlan(result_id=str(uuid4()),source_command_id=str(uuid4()),source_run_id=uuid4().hex,
        task_id=uuid4().hex,project_id=str(uuid4()),input_id=captured.input_id,source_generation=1,output_directory='.nyt-output')
    boundary=ResultBoundary(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
    return workspace,inputs,plan,boundary,limits


def next_plan(plan):
    return plan.model_copy(update={'result_id':str(uuid4()),'source_command_id':str(uuid4()),'source_run_id':uuid4().hex})


def artifact(result,role):
    state=json.loads((result/'result.json').read_text())
    entry=next(f['artifact'] for f in state['bundle']['files'] if f['role']==role)
    return (result/'files'/entry['artifact_id']).read_bytes()


def test_baseline_is_verified_once_for_distinct_results(tmp_path):
    root,inputs,plan,boundary,limits=setup_capture(tmp_path)
    calls=[];previous=sys.getprofile()
    def profile(frame,event,arg):
        if event=='call' and frame.f_code is archive.validate_package.__code__:calls.append(1)
    sys.setprofile(profile)
    try:
        freeze_result(root,inputs,tmp_path/'spools'/'first',plan,boundary,1,ResultLimits(),limits)
        freeze_result(root,inputs,tmp_path/'spools'/'second',next_plan(plan),boundary,1,ResultLimits(),limits)
    finally:sys.setprofile(previous)
    assert len(calls)==1,'immutable I must not be unpacked and validated on every round'


def test_scan_freezes_only_new_content_and_checks_raw_blob_ids(tmp_path):
    from naiyue_test_agent.cloud_runner.result_scan import freeze_tree
    root,inputs,plan,boundary,limits=setup_capture(tmp_path)
    package=InputPackage.model_validate_json((inputs/'input.json').read_bytes())
    known={}
    for entry in package.manifest.entries:
        data=(root/entry.path).read_bytes()
        known[(entry.size,entry.sha256)]=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    changed=bytes(reversed(range(256)))*32;(root/'change.bin').write_bytes(changed)
    (root/'renamed.txt').write_bytes((root/'keep.txt').read_bytes());(root/'mode.sh').chmod(0o755)
    destination=tmp_path/'frozen'
    entries,blobs,proof,oids=freeze_tree(root,destination,limits,ResultLimits(),package=package,known_content=known)
    assert list(blobs)==['change.bin'] and len(list(destination.iterdir()))==1
    assert blobs['change.bin'].read_bytes()==changed
    assert oids['change.bin']==hashlib.sha1(b'blob 8192\0'+changed).hexdigest()
    assert oids['keep.txt']==oids['renamed.txt']==known[(6,hashlib.sha256(b'keep\r\n').hexdigest())]
    assert next(e for e in entries if e.path=='mode.sh').mode==0o755


def test_cache_damage_is_rejected_without_publishing_new_result(tmp_path):
    root,inputs,plan,boundary,limits=setup_capture(tmp_path);spools=tmp_path/'spools'
    freeze_result(root,inputs,spools/'first',plan,boundary,1,ResultLimits(),limits)
    cache=spools/'.baseline-cache'/plan.input_id
    objects=list(cache.glob('objects.git/objects/??/*'))
    assert objects,'I must have a private reusable object database outside each spool'
    damaged=objects[0];raw=damaged.read_bytes();damaged.chmod(0o600);damaged.write_bytes(b'x'+raw[1:])
    with pytest.raises(CloudError,match='result_baseline_changed'):
        freeze_result(root,inputs,spools/'second',next_plan(plan),boundary,1,ResultLimits(),limits)
    assert not (spools/'second').exists()


def test_warm_capture_reads_same_size_same_mtime_changes_and_does_not_grow_baseline(tmp_path):
    root,inputs,plan,boundary,limits=setup_capture(tmp_path);spools=tmp_path/'spools'
    freeze_result(root,inputs,spools/'first',plan,boundary,1,ResultLimits(),limits)
    cache=spools/'.baseline-cache'/plan.input_id
    before={p.relative_to(cache).as_posix():p.read_bytes() for p in cache.rglob('*') if p.is_file()}
    changed=root/'change.bin';stat=changed.stat();raw=bytes(reversed(range(256)))*32
    changed.write_bytes(raw);os.utime(changed,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    second=freeze_result(root,inputs,spools/'second',next_plan(plan),boundary,1,ResultLimits(),limits)
    manifest=json.loads(artifact(second,'manifest'))
    assert next(e for e in manifest['entries'] if e['path']=='change.bin')['sha256']==hashlib.sha256(raw).hexdigest()
    assert b'GIT binary patch' in artifact(second,'patch')
    assert before=={p.relative_to(cache).as_posix():p.read_bytes() for p in cache.rglob('*') if p.is_file()}
    assert {p.name for p in spools.iterdir()}=={'.baseline-cache','first','second'}


@pytest.mark.parametrize('empty',[False,True])
def test_raw_output_trees_match_native_git_including_directory_sort_modes_and_links(tmp_path,empty):
    from naiyue_test_agent.cloud_runner.result_git import ResultGit
    from naiyue_test_agent.cloud_files.paths import identity
    native=ResultGit(tmp_path/'native',ResultLimits());optimized=ResultGit(tmp_path/'optimized',ResultLimits())
    entries=[];blobs={};data=tmp_path/'data';data.mkdir()
    if not empty:
        # Git sorts a directory as if its name ended in '/', after a same-prefix '.' file.
        for number,(path,kind,mode,content) in enumerate([
            ('dir/file.txt','file',0o644,b'a\r\n'),('dir.sh','file',0o755,b'echo ok\n'),
            ('dir0.txt','file',0o644,b'after dir'),('dir/link','symlink',0o777,b'file.txt'),
            ('nested/deep/\u4e2d\u6587.bin','file',0o644,bytes(range(256))),('dir/sub/x','file',0o644,b'x')]):
            blob=data/str(number);blob.write_bytes(content);blobs[path]=blob
            entries.append(SimpleNamespace(path=path,type=kind,mode=mode))
    expected=native.tree(entries,blobs,'native');hashes=optimized.hashes(blobs)
    actual=optimized.tree_oids(entries,hashes)
    assert actual==expected and optimized.entries(actual)==native.entries(expected)
    assert optimized.run(['fsck','--full','--no-reflogs'],check=False)[0]==0
    # Native write-tree can touch same-OID alternate trees; this path must not.
    before={p:identity(p.stat()) for p in native.path.rglob('*') if p.is_file()}
    known={p.parent.name+p.name for p in native.path.glob('objects/??/*')}
    reuse=ResultGit(tmp_path/'reuse',ResultLimits(),alternate=native.path/'objects')
    assert reuse.tree_oids(entries,hashes,known_objects=known)==expected
    patch=tmp_path/'empty.patch';reuse.patch(expected,expected,patch,100)
    assert patch.read_bytes()==b'' and reuse.entries(expected)==native.entries(expected)
    assert not list(reuse.path.glob('objects/??/*'))
    assert before=={p:identity(p.stat()) for p in native.path.rglob('*') if p.is_file()}


def test_source_change_between_hash_and_changed_file_freeze_is_rejected(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_runner import result_scan
    root,inputs,plan,boundary,limits=setup_capture(tmp_path);spools=tmp_path/'spools'
    freeze_result(root,inputs,spools/'first',plan,boundary,1,ResultLimits(),limits)
    (root/'change.bin').write_bytes(b'new content')
    original=result_scan.read_content
    def change_after_hash(source,path,expected,lim,*,target=None):
        value=original(source,path,expected,lim,target=target)
        if path=='change.bin' and target is None:(root/path).write_bytes(b'other bytes')
        return value
    monkeypatch.setattr(result_scan,'read_content',change_after_hash)
    with pytest.raises(CloudError,match='input_source_changed|result_source_changed'):
        freeze_result(root,inputs,spools/'second',next_plan(plan),boundary,1,ResultLimits(),limits)
    assert not (spools/'second').exists()
