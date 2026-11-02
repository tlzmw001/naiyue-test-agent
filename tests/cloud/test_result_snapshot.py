"""Raw I→O comparison and report capture, independent of business execution."""
import hashlib,json,os,subprocess
from pathlib import Path
from uuid import uuid4
import pytest
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.contracts.cloud_results import ResultLimits,ResultPlan,ResultBoundary
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result


def fixture(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'a.txt').write_bytes(b'old\r\n')
    (source/'old.bin').write_bytes(bytes(range(256))*2);(source/'remove.txt').write_text('remove')
    inp=tmp_path/'input';lim=InputLimits();captured=capture(source,{'paths':['a.txt','old.bin','remove.txt']},inp,lim)
    root=tmp_path/'work';root.mkdir();(root/'a.txt').write_bytes(b'new\r\n');(root/'old.bin').write_bytes(bytes(reversed(range(256)))*2)
    (root/'new.sh').write_text('#!/bin/sh\necho done\n');(root/'new.sh').chmod(0o755)
    plan=ResultPlan(result_id=str(uuid4()),source_command_id=str(uuid4()),source_run_id=uuid4().hex,
        task_id=uuid4().hex,project_id=str(uuid4()),input_id=captured.input_id,source_generation=1,output_directory='.nyt-output')
    output=root/plan.output_directory/plan.source_run_id;output.mkdir(parents=True);(output/'report.json').write_text('{"passed":1}')
    other=root/plan.output_directory/('f'*32);other.mkdir();(other/'old-report.json').write_text('stale')
    boundary=ResultBoundary(kind='run',epoch=str(uuid4()),seq=8,outcome='completed')
    return root,inp,plan,boundary,lim


def test_raw_binary_patch_manifest_reports_and_no_source_git_mutation(tmp_path):
    root,inp,plan,boundary,lim=fixture(tmp_path)
    subprocess.run(['git','init','-q',str(root)],check=True)
    before=(root/'.git/config').read_bytes()
    result=freeze_result(root,inp,tmp_path/'spool',plan,boundary,1,ResultLimits(),lim)
    metadata=json.loads((result/'result.json').read_text());files=metadata['bundle']['files']
    assert {f['role'] for f in files}=={'manifest','patch','artifact'}
    report=next(f for f in files if f['role']=='artifact');assert report['artifact']['relative_name']=='report.json'
    assert all('old-report' not in f['artifact']['relative_name'] for f in files)
    manifest=next(f for f in files if f['role']=='manifest');body=json.loads((result/'files'/manifest['artifact']['artifact_id']).read_text())
    assert [e['path'] for e in body['entries']]==['a.txt','new.sh','old.bin']
    assert next(e for e in body['entries'] if e['path']=='new.sh')['mode']==0o755
    patch=next(f for f in files if f['role']=='patch');raw=(result/'files'/patch['artifact']['artifact_id']).read_bytes()
    assert b'GIT binary patch' in raw and b'deleted file mode' in raw and b'new file mode 100755' in raw
    assert b'.nyt-output' not in raw and (root/'.git/config').read_bytes()==before
    assert not (root/'.git/index').exists() and not list((root/'.git/objects').glob('??/*'))
    for f in files:
        raw=(result/'files'/f['artifact']['artifact_id']).read_bytes()
        assert len(raw)==f['artifact']['size'] and hashlib.sha256(raw).hexdigest()==f['artifact']['sha256']
    # Apply inside a disposable checkout to prove the generated raw patch is useful.
    from naiyue_test_agent.cloud_files.archive import validate_package
    package=json.loads((inp/'input.json').read_text());dest=tmp_path/'apply';dest.mkdir()
    validate_package(inp/'archive',package['codec'],lim,expected_id=plan.input_id,destination=dest)
    applied=subprocess.run(['git','apply','--binary','--whitespace=nowarn',str(result/'files'/patch['artifact']['artifact_id'])],cwd=dest,capture_output=True)
    assert applied.returncode==0,applied.stderr
    assert (dest/'a.txt').read_bytes()==b'new\r\n' and (dest/'old.bin').read_bytes()==(root/'old.bin').read_bytes()
    assert not (dest/'remove.txt').exists()


def test_frozen_result_retry_does_not_rescan_a_changed_workspace(tmp_path):
    root,inp,plan,boundary,lim=fixture(tmp_path);path=tmp_path/'spool'
    first=freeze_result(root,inp,path,plan,boundary,1,ResultLimits(),lim)
    frozen=(first/'result.json').read_bytes();(root/'a.txt').write_text('later')
    assert freeze_result(root,inp,path,plan,boundary,1,ResultLimits(),lim)==first
    assert (first/'result.json').read_bytes()==frozen


def test_capture_rejects_fifo_and_escaping_links_without_waiting(tmp_path):
    root,inp,plan,boundary,lim=fixture(tmp_path)
    (root/'escape').symlink_to('../../outside')
    with pytest.raises(CloudError):freeze_result(root,inp,tmp_path/'bad-link',plan,boundary,1,ResultLimits(),lim)
    (root/'escape').unlink();os.mkfifo(root/'fifo')
    with pytest.raises(CloudError):freeze_result(root,inp,tmp_path/'bad-fifo',plan,boundary,1,ResultLimits(),lim)


def test_output_keeps_safe_linux_names_for_later_portability_review(tmp_path):
    root,inp,plan,boundary,lim=fixture(tmp_path);(root/'CON').write_text('linux file')
    result=freeze_result(root,inp,tmp_path/'spool',plan,boundary,1,ResultLimits(),lim)
    metadata=json.loads((result/'result.json').read_text());manifest=next(f for f in metadata['bundle']['files'] if f['role']=='manifest')
    output=json.loads((result/'files'/manifest['artifact']['artifact_id']).read_text())
    assert any(e['path']=='CON' for e in output['entries'])


def test_budget_rejection_does_not_publish_partial_result(tmp_path):
    root,inp,plan,boundary,lim=fixture(tmp_path);target=tmp_path/'spool'
    with pytest.raises(CloudError):freeze_result(root,inp,target,plan,boundary,1,ResultLimits(max_patch_bytes=1),lim)
    assert not target.exists()


def test_original_cache_file_is_not_reported_deleted(tmp_path):
    from dataclasses import replace
    from naiyue_test_agent.contracts.cloud_inputs import Selection
    source=tmp_path/'s';(source/'.cache').mkdir(parents=True);(source/'.cache/keep.py').write_text('keep')
    inp=tmp_path/'i';lim=InputLimits();cap=capture(source,Selection(paths=['.cache/keep.py']),inp,lim)
    plan=ResultPlan(result_id=str(uuid4()),source_command_id=str(uuid4()),source_run_id=uuid4().hex,task_id=uuid4().hex,
        project_id=str(uuid4()),input_id=cap.input_id,source_generation=1,output_directory='.nyt-output')
    bound=ResultBoundary(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
    result=freeze_result(source,inp,tmp_path/'o',plan,bound,1,ResultLimits(),lim)
    state=json.loads((result/'result.json').read_text());m=next(f['artifact'] for f in state['bundle']['files'] if f['role']=='manifest')
    assert [e['path'] for e in json.loads((result/'files'/m['artifact_id']).read_text())['entries']]==['.cache/keep.py']


def test_frozen_global_ignore_applies_without_reading_live_config(tmp_path):
    from types import SimpleNamespace
    from naiyue_test_agent.cloud_runner.result_git import ignored_paths
    policy=SimpleNamespace(rules=[SimpleNamespace(kind='ignore',name='global/ignore',content='*.private\n'),
        SimpleNamespace(kind='ignore',name='worktree/.gitignore',content='!allowed.private\n')])
    assert ignored_paths(policy,['secret.private','allowed.private','a.py'],tmp_path/'policy',ResultLimits())=={'secret.private'}
