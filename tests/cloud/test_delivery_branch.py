"""Branch delivery preserves the user's current checkout and frozen Git policy."""
import json,os,subprocess
from pathlib import Path
from uuid import uuid4
import pytest
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_files.sources import select_git
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result
from naiyue_test_agent.contracts.cloud_inputs import InputLimits,InputPackage
from naiyue_test_agent.contracts.cloud_results import ResultLimits,ResultPlan,ResultBoundary
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def git(root,*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout


def branch_fixture(tmp_path,*,attributes='*.txt text eol=crlf\n',autocrlf='false'):
    from naiyue_test_agent.cloud_files.delivery_verify import verify_delivery
    source=tmp_path/'source';source.mkdir();git(source,'init','--quiet')
    git(source,'config','user.name','fixture');git(source,'config','user.email','fixture@example.invalid')
    git(source,'config','core.autocrlf',autocrlf)
    (source/'.gitattributes').write_text('*.txt text eol=crlf\n');(source/'readme.txt').write_bytes(b'committed\r\n')
    (source/'remove.txt').write_text('removed before capture');(source/'unmanaged.bin').write_bytes(b'committed unmanaged')
    git(source,'add','.');git(source,'update-index','--add','--cacheinfo','160000,'+'1'*40+',submodule')
    git(source,'commit','--quiet','-m','original')
    (source/'.gitattributes').write_text(attributes)
    (source/'readme.txt').write_bytes(b'local WIP\r\n');(source/'remove.txt').unlink();(source/'wip.py').write_bytes(b'local untracked\n')
    ilimits=InputLimits();plan=select_git(source,ilimits,exclude=['unmanaged.bin'])
    captured=capture(source,plan.selection,tmp_path/'input',ilimits,source_receipt=plan.receipt)
    package=InputPackage.model_validate_json((captured.directory/'input.json').read_bytes())
    output=tmp_path/'output';output.mkdir()
    for entry in package.manifest.entries:(output/entry.path).write_bytes((source/entry.path).read_bytes())
    (output/'readme.txt').write_bytes(b'cloud result\r\n');(output/'added.txt').write_bytes(b'new file\r\n')
    result_plan=ResultPlan(result_id=str(uuid4()),source_command_id=str(uuid4()),source_run_id=uuid4().hex,task_id=uuid4().hex,
        project_id=str(uuid4()),input_id=package.input_id,source_generation=1,output_directory='.nyt-output')
    boundary=ResultBoundary(kind='run',epoch=str(uuid4()),seq=1,outcome='completed')
    spool=freeze_result(output,captured.directory,tmp_path/'spool',result_plan,boundary,1,ResultLimits(),ilimits)
    result=json.loads((spool/'result.json').read_bytes());roles={f['role']:spool/'files'/f['artifact']['artifact_id'] for f in result['bundle']['files']}
    verified=verify_delivery(package,captured.directory/'archive',roles['manifest'].read_bytes(),roles['patch'],tmp_path/'verify',ilimits,ResultLimits())
    return source,captured,verified


def source_state(source):
    return dict(head=git(source,'rev-parse','HEAD'),index=(source/'.git/index').read_bytes(),
        files={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file() and '.git' not in p.relative_to(source).parts},
        objects=sorted(p.relative_to(source/'.git/objects').as_posix() for p in (source/'.git/objects').rglob('*') if p.is_file()))


def test_branch_preserves_wip_initial_deletes_unmanaged_gitlinks_and_frozen_attrs(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_branch import branch_delivery,branch_preflight
    source,captured,verified=branch_fixture(tmp_path)
    # Current attrs and unrelated dirty content are not the captured policy.
    (source/'.gitattributes').write_text('*.txt -text\n');(source/'unmanaged.bin').write_bytes(b'current dirty')
    before=source_state(source)
    assert branch_preflight(verified,captured.source_receipt,source,'cloud/result',ResultLimits())==[]
    assert source_state(source)==before
    value=branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits(),journal=tmp_path/'branch.json')
    assert value['branch_head']==git(source,'rev-parse','cloud/result').decode().strip()
    assert git(source,'show','cloud/result:readme.txt')==b'cloud result\n'
    assert git(source,'show','cloud/result:added.txt')==b'new file\n'
    assert git(source,'show','cloud/result:wip.py')==b'local untracked\n'
    assert git(source,'show','cloud/result:unmanaged.bin')==b'committed unmanaged'
    assert b'remove.txt' not in git(source,'ls-tree','-r','--name-only','cloud/result')
    assert git(source,'ls-tree','cloud/result','submodule').startswith(b'160000 commit '+'1'.encode()*40)
    after=source_state(source);assert {k:v for k,v in after.items() if k!='objects'}=={k:v for k,v in before.items() if k!='objects'}
    assert git(source,'rev-parse','cloud/result^')==before['head']


@pytest.mark.parametrize('attributes',['*.txt filter=custom\n','*.txt working-tree-encoding=UTF-16\n','*.txt ident\n'])
def test_branch_rejects_unsupported_frozen_attributes_before_source_mutation(tmp_path,attributes):
    from naiyue_test_agent.cloud_files.delivery_branch import branch_preflight
    source,captured,verified=branch_fixture(tmp_path,attributes=attributes)
    before=source_state(source)
    with pytest.raises(CloudError,match='cloud_delivery_attributes_unsupported'):
        branch_preflight(verified,captured.source_receipt,source,'cloud/result',ResultLimits())
    assert source_state(source)==before


def test_branch_lost_ref_ack_retries_same_expected_commit_and_rejects_other_ref(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files.delivery_branch import branch_delivery
    from naiyue_test_agent.cloud_files.delivery_git import DeliveryGit
    source,captured,verified=branch_fixture(tmp_path);journal=tmp_path/'branch.json';original=DeliveryGit.repository
    repo=DeliveryGit.repository(source,ResultLimits());real=repo.run;failed=False
    def lost(args,**kwargs):
        nonlocal failed
        value=real(args,**kwargs)
        if 'update-ref' in args and not failed:failed=True;raise CloudError('controlled_lost_ack',503)
        return value
    monkeypatch.setattr(repo,'run',lost)
    monkeypatch.setattr(DeliveryGit,'repository',staticmethod(lambda path,limits:repo if Path(path)==source else original(path,limits)))
    with pytest.raises(CloudError,match='controlled_lost_ack'):
        branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits(),journal=journal)
    head=git(source,'rev-parse','cloud/result').decode().strip()
    assert json.loads(journal.read_bytes())['commit']==head
    assert branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits(),journal=journal)=={'branch_head':head}
    git(source,'update-ref','refs/heads/cloud/result',git(source,'rev-parse','HEAD').decode().strip())
    with pytest.raises(CloudError,match='cloud_delivery_branch_conflict'):
        branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits(),journal=journal)


def test_output_attributes_cannot_activate_filters_during_normalization(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_branch import branch_delivery
    source,captured,verified=branch_fixture(tmp_path)
    # The verified O may change .gitattributes; only the frozen input rules govern this delivery.
    from dataclasses import replace
    import hashlib
    content=b'*.txt filter=custom\n';path=verified.directory/'.gitattributes';path.write_bytes(content)
    entries=[e.model_copy(update={'size':len(content),'sha256':hashlib.sha256(content).hexdigest()}) if e.path=='.gitattributes' else e for e in verified.output_manifest.entries]
    verified=replace(verified,output_manifest=verified.output_manifest.model_copy(update={'entries':entries}))
    marker=tmp_path/'filter-was-run';git(source,'config','filter.custom.clean','touch '+str(marker))
    value=branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits())
    assert not marker.exists() and git(source,'show',value['branch_head']+':added.txt')==b'new file\n'


def test_branch_preflight_rejects_a_linked_ref_parent(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_branch import branch_preflight
    source,captured,verified=branch_fixture(tmp_path);outside=tmp_path/'outside';outside.mkdir()
    (source/'.git/refs/heads/cloud').symlink_to(outside,target_is_directory=True)
    with pytest.raises(CloudError):branch_preflight(verified,captured.source_receipt,source,'cloud/result',ResultLimits())
    assert list(outside.iterdir())==[]


def test_branch_journal_is_not_published_before_its_bytes_are_durable(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files import delivery_branch
    source,captured,verified=branch_fixture(tmp_path);journal=tmp_path/'branch.json'
    original_open=delivery_branch.open_store_file;original_sync=os.fsync;journal_fds=set();failed=False
    def opening(path,**kwargs):
        fd=original_open(path,**kwargs)
        if Path(path).name.startswith('branch.json'):journal_fds.add(fd)
        return fd
    def interrupted(fd):
        nonlocal failed
        if fd in journal_fds and not failed:failed=True;raise OSError('controlled journal flush failure')
        return original_sync(fd)
    monkeypatch.setattr(delivery_branch,'open_store_file',opening);monkeypatch.setattr(delivery_branch.os,'fsync',interrupted)
    with pytest.raises(CloudError):delivery_branch.branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits(),journal=journal)
    assert not journal.exists()
    missing=subprocess.run(['git','-C',str(source),'show-ref','--verify','--quiet','refs/heads/cloud/result'])
    assert missing.returncode==1
    ready=delivery_branch.branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits(),journal=journal)
    assert json.loads(journal.read_bytes())['commit']==ready['branch_head']


def test_missing_original_head_is_a_typed_preflight_failure(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_branch import branch_preflight
    source,captured,verified=branch_fixture(tmp_path)
    receipt=json.loads(captured.source_receipt.read_bytes());receipt['head']='0'*40
    captured.source_receipt.write_text(json.dumps(receipt));before=source_state(source)
    with pytest.raises(CloudError):branch_preflight(verified,captured.source_receipt,source,'cloud/result',ResultLimits())
    assert source_state(source)==before


@pytest.mark.parametrize('autocrlf,expected',[('true',b'new file\n'),('input',b'new file\n'),('false',b'new file\r\n')])
def test_branch_uses_frozen_autocrlf_for_new_paths(tmp_path,autocrlf,expected):
    from naiyue_test_agent.cloud_files.delivery_branch import branch_delivery
    source,captured,verified=branch_fixture(tmp_path,attributes='',autocrlf=autocrlf)
    git(source,'config','core.autocrlf','false' if autocrlf!='false' else 'true')
    value=branch_delivery(verified,captured.source_receipt,source,'cloud/result',ResultLimits())
    assert git(source,'show',value['branch_head']+':added.txt')==expected
