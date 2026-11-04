"""Exercise Windows locale and stat boundaries without pretending to run Win32."""
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.cloud_inputs import InputLimits,InputPackage,Selection,input_path
from naiyue_test_agent.contracts.cloud_results import ResultLimits
from naiyue_test_agent.contracts.cloud_sources import SourcePlan,SourcePolicy,SourceRule,SourceReceipt
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.fixture(params=['gbk','cp1252'])
def legacy_locale(monkeypatch,request):
    # Model only unspecified text encodings; binary IO and explicit UTF-8 remain real.
    original=io.open
    def opened(file,mode='r',buffering=-1,encoding=None,errors=None,newline=None,closefd=True,opener=None):
        if 'b' not in mode and encoding in (None,'locale'):encoding=request.param
        return original(file,mode,buffering,encoding,errors,newline,closefd,opener)
    monkeypatch.setattr(io,'open',opened)


@pytest.mark.parametrize('kind',['selection','git'])
def test_unicode_capture_metadata_can_be_uploaded_and_read_back(tmp_path,legacy_locale,kind):
    from naiyue_test_agent.cloud_files.capture import capture
    from naiyue_test_agent.cloud_files.input_upload import upload
    from naiyue_test_agent.cloud_files.sources import select_git
    from test_git_source_plan import git
    source=tmp_path/'项目';source.mkdir();name='报告.txt'
    (source/name).write_bytes('原始内容\r\n'.encode())
    plan=None
    if kind=='git':
        git(source,'init');git(source,'add','.')
        plan=select_git(source,InputLimits())
    captured=capture(source,plan.selection if plan else Selection(paths=[name]),tmp_path/'材料',InputLimits(),
        source_receipt=plan.receipt if plan else None)
    package=InputPackage.model_validate_json((captured.directory/'input.json').read_bytes())
    assert package.selection.paths==[name]
    client=SimpleNamespace(request=lambda *args:package.model_dump())
    assert upload(client,captured.directory)==dict(input_id=captured.input_id,uploaded_bytes=0,cache_hit=True)
    if plan:assert SourceReceipt.model_validate_json((captured.directory/'source.json').read_bytes())==plan.receipt
    assert (source/name).read_bytes()=='原始内容\r\n'.encode()


def test_unicode_cli_selection_plan_is_utf8(tmp_path,legacy_locale):
    from naiyue_test_agent.cli.cloud_inputs import execute
    from test_git_source_plan import git
    source=tmp_path/'项目';source.mkdir();(source/'说明.txt').write_bytes(b'input')
    git(source,'init');git(source,'add','.')
    path=tmp_path/'plan.json'
    execute(SimpleNamespace(input_command='select',source=source,limits=None,include=[],exclude=[],output=path))
    plan=SourcePlan.model_validate_json(path.read_bytes())
    assert plan.receipt.root==source and plan.selection['paths']==['说明.txt']


@pytest.mark.parametrize('kind',['delivery','workflow'])
@pytest.mark.parametrize('text',['中文目录/报告.txt','报告🧪.txt'])
def test_unicode_journals_survive_restart(tmp_path,legacy_locale,kind,text):
    value={'path':text,'commands':{}};path=tmp_path/'journal.json'
    if kind=='delivery':
        from naiyue_test_agent.cloud_runner.state import read_json,write_json
        write_json(path,value);assert read_json(path)==value
    else:
        from scripts.cloud_workflow.journal import Journal
        Journal(path,value);assert Journal(path,{}).value==value
    assert json.loads(path.read_bytes())==value


def test_unicode_ignore_policy_preserves_git_matching(tmp_path,legacy_locale):
    from naiyue_test_agent.cloud_runner.result_git import ignored_paths
    policy=SourcePolicy(kind='git',rules=[SourceRule(name='worktree/.gitignore',kind='ignore',content='缓存/\n')])
    assert ignored_paths(policy,['缓存/a.txt','keep.txt'],tmp_path/'policy',ResultLimits())=={'缓存/a.txt'}


@pytest.mark.parametrize('field',[None,'st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'])
def test_partial_export_accepts_windows_clock_semantics_but_rejects_changes(tmp_path,monkeypatch,field):
    from naiyue_test_agent.cloud_files import delivery_apply as module
    from test_delivery_apply import output
    root,verified=output(tmp_path);original_copy=module.shutil.copytree
    def interrupted(source,destination,**kwargs):
        (destination/'a').write_bytes(b'ne');raise OSError('copy interrupted')
    monkeypatch.setattr(module.shutil,'copytree',interrupted)
    with pytest.raises(OSError,match='copy interrupted'):module.export_tree(root,'exported',verified,tmp_path/'journal')
    monkeypatch.setattr(module.shutil,'copytree',original_copy)
    stage,=root.glob('.nyt-export-*');inode=(stage/'a').stat().st_ino
    original_stat=os.fstat;calls=0
    def descriptor_stat(fd):
        nonlocal calls
        info=original_stat(fd)
        if info.st_ino!=inode:return info
        calls+=1;fields={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
        fields['st_ctime_ns']+=1_000_000_000
        if field is not None and calls>=2:fields[field]+=1
        return SimpleNamespace(**fields)
    monkeypatch.setattr(os,'fstat',descriptor_stat)
    if field is None:
        module.export_tree(root,'exported',verified,tmp_path/'journal')
        assert (root/'exported/a').read_bytes()==b'new\r\n'
        assert (root/'exported/b').read_bytes()==b'\0binary'
    else:
        with pytest.raises(CloudError):module.export_tree(root,'exported',verified,tmp_path/'journal')
        assert (stage/'a').read_bytes()==b'ne' and not (root/'exported').exists()


@pytest.mark.parametrize('device',['COM¹','COM²','COM³','LPT¹','LPT²','LPT³'])
@pytest.mark.parametrize('pattern',['{}','dir/{}.txt'])
def test_windows_superscript_devices_are_rejected_before_filesystem_io(device,pattern):
    with pytest.raises(ValueError,match='invalid_input_path'):input_path(pattern.format(device))


@pytest.mark.parametrize('field',[None,'st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'])
def test_code_preflight_compares_each_windows_clock_with_itself(tmp_path,monkeypatch,field):
    from naiyue_test_agent.cloud_files.delivery_review import observed_entry
    target=tmp_path/'报告.txt';target.write_bytes(b'local bytes');inode=target.stat().st_ino
    original=os.fstat;calls=0
    def descriptor_stat(fd):
        nonlocal calls
        info=original(fd)
        if info.st_ino!=inode:return info
        calls+=1;fields={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
        fields['st_ctime_ns']+=1_000_000_000
        if field is not None and calls>=2:fields[field]+=1
        return SimpleNamespace(**fields)
    monkeypatch.setattr(os,'fstat',descriptor_stat)
    if field is None:
        entry=observed_entry(tmp_path,target.name,1024)
        import hashlib
        assert (entry.size,entry.sha256)==(11,hashlib.sha256(b'local bytes').hexdigest())
    else:
        with pytest.raises(CloudError):observed_entry(tmp_path,target.name,1024)
    assert target.read_bytes()==b'local bytes'


@pytest.mark.parametrize('encoding',['gbk','cp1252'])
@pytest.mark.parametrize('command',['input','task','release'])
def test_cloud_cli_json_can_be_redirected_in_legacy_windows_encoding(tmp_path,monkeypatch,encoding,command):
    import sys
    from naiyue_test_agent.cli.cloud import main
    from naiyue_test_agent.cli import cloud_inputs,cloud_tasks,cloud_release
    expected={'path':'中文目录/报告🧪.txt','command_id':'original-operation'}
    module={'input':cloud_inputs,'task':cloud_tasks,'release':cloud_release}[command]
    monkeypatch.setattr(module,'execute',lambda args:expected)
    args={'input':['input','select','--source',str(tmp_path),'--output',str(tmp_path/'plan.json')],
        'task':['task','show','a'*32,'--settings',str(tmp_path/'connection.json')],
        'release':['release','discover','--remote',str(tmp_path/'remote.json')]}[command]
    raw=io.BytesIO();stream=io.TextIOWrapper(raw,encoding=encoding)
    with monkeypatch.context() as patch:
        patch.setattr(sys,'stdout',stream)
        assert main(args)==0
        stream.flush()
    assert json.loads(raw.getvalue())==expected
