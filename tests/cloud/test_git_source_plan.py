"""Real Git metadata is batched; capture freezes bytes without Git mutations."""
import subprocess
import pytest
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def git(root,*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout


def repository(tmp_path):
    root=tmp_path/'source';root.mkdir();git(root,'init')
    (root/'.gitignore').write_text('ignored/\n*.cache\n')
    (root/'.gitattributes').write_text('*.txt text eol=crlf\n')
    (root/'run.sh').write_bytes(b'#!/bin/sh\necho ok\n');(root/'readme.txt').write_bytes(b'raw\r\nbytes\r\n')
    git(root,'add','.');git(root,'update-index','--chmod=+x','run.sh')
    (root/'api.proto').write_text('proto fixture');(root/'ignored').mkdir();(root/'ignored/secret.txt').write_text('ignored')
    (root/'.env').write_text('not selected');(root/'out.cache').write_text('ignored cache')
    return root


def test_git_plan_uses_ignore_index_modes_and_keeps_local_source_facts(tmp_path):
    from naiyue_test_agent.cloud_files.sources import select_git
    from naiyue_test_agent.cloud_files.capture import capture
    root=repository(tmp_path);plan=select_git(root,InputLimits())
    assert plan.selection['schema']==2
    assert plan.selection['paths']==['.gitattributes','.gitignore','api.proto','readme.txt','run.sh']
    assert 'run.sh' in plan.selection['executables'] and plan.receipt.git_commands<=6
    assert plan.receipt.attributes['readme.txt']['eol']=='crlf'
    result=capture(root,plan.selection,tmp_path/'capture',InputLimits(),source_receipt=plan.receipt)
    assert (result.directory/'source.json').exists()
    assert str(root).encode() not in (result.directory/'selection.json').read_bytes()
    assert (root/'readme.txt').read_bytes()==b'raw\r\nbytes\r\n'


@pytest.mark.parametrize('change',['ignore','index','membership'])
def test_source_plan_changes_require_reselection(tmp_path,change):
    from naiyue_test_agent.cloud_files.sources import select_git
    from naiyue_test_agent.cloud_files.capture import capture
    root=repository(tmp_path);plan=select_git(root,InputLimits())
    if change=='ignore':(root/'.gitignore').write_text('different rule\n')
    elif change=='index':git(root,'update-index','--chmod=-x','run.sh')
    else:(root/'late.proto').write_text('not silently omitted')
    with pytest.raises(CloudError,match='source_plan_changed'):
        capture(root,plan.selection,tmp_path/'capture',InputLimits(),source_receipt=plan.receipt)
    assert not (tmp_path/'capture').exists()


@pytest.mark.parametrize('initial',['[user]\nname = fixture\n',''])
def test_git_include_sources_are_frozen_even_without_current_core_settings(tmp_path,initial):
    from naiyue_test_agent.cloud_files.sources import select_git
    from naiyue_test_agent.cloud_files.capture import capture
    root=repository(tmp_path);external=tmp_path/'included-config';external.write_text(initial)
    git(root,'config','include.path',str(external))
    plan=select_git(root,InputLimits());external.write_text(initial+'[core]\nautocrlf = true\n')
    with pytest.raises(CloudError,match='source_plan_changed'):
        capture(root,plan.selection,tmp_path/'capture',InputLimits(),source_receipt=plan.receipt)


def test_git_submodule_descendants_require_separate_source(tmp_path):
    from naiyue_test_agent.cloud_files.sources import select_git
    root=repository(tmp_path);sub=root/'sub';sub.mkdir();(sub/'nested.txt').write_text('separate')
    git(root,'update-index','--add','--cacheinfo','160000,'+'1'*40+',sub')
    with pytest.raises(CloudError,match='input_submodule_unsupported'):
        select_git(root,InputLimits(),include=['sub/nested.txt'])


@pytest.mark.parametrize('before,after',[(0o755,0o644),(0o644,0o755)])
def test_untracked_execution_mode_change_requires_reselection(tmp_path,before,after):
    from naiyue_test_agent.cloud_files.sources import select_git
    from naiyue_test_agent.cloud_files.capture import capture
    root=repository(tmp_path);run=root/'new.sh';run.write_bytes(b'#!/bin/sh\necho ok\n');run.chmod(before)
    plan=select_git(root,InputLimits());run.chmod(after)
    with pytest.raises(CloudError,match='source_plan_changed'):
        capture(root,plan.selection,tmp_path/'capture',InputLimits(),source_receipt=plan.receipt)
