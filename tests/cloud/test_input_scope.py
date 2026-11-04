from pathlib import Path
import subprocess
import pytest
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_two_gib_defaults_and_staging_headroom():
    v=InputLimits()
    assert v.max_input_bytes==2*1024**3
    assert v.max_file_bytes==v.max_input_bytes
    assert v.max_archive_bytes>=v.max_input_bytes+v.max_files*8192+v.max_metadata_bytes*2
    assert v.max_cache_bytes>v.max_input_bytes*2+v.max_files*8192+v.max_metadata_bytes*2
    assert v.max_archive_bytes<=v.chunk_bytes*v.max_segments
    assert v.max_files>=32768


def test_non_git_detected_before_capture(tmp_path):
    from naiyue_test_agent.cloud_files.source_scope import capabilities,plan_scope
    assert capabilities(tmp_path,InputLimits())['git_error']=='input_git_required'
    with pytest.raises(CloudError,match='input_git_required'):
        plan_scope(tmp_path,InputLimits(),mode='git')


def test_directories_and_windows_paths_are_expanded_safely(tmp_path):
    from naiyue_test_agent.cloud_files.source_scope import plan_scope
    (tmp_path/'src').mkdir();(tmp_path/'src'/'a.py').write_text('abc')
    (tmp_path/'src'/'b.py').write_text('def')
    (tmp_path/'src'/'.env').write_text('secret')
    selection,_=plan_scope(tmp_path,InputLimits(),mode='selection',paths=['src','src\\a.py'])
    assert selection.paths==['src/a.py','src/b.py']
    for path in ['../outside','C:\\outside','src/*.py']:
        with pytest.raises(CloudError):plan_scope(tmp_path,InputLimits(),mode='selection',paths=[path])


def test_scope_preview_reports_budget_without_capture(tmp_path):
    from naiyue_test_agent.cloud_files.source_scope import preview_scope
    (tmp_path/'large').write_bytes(b'12345')
    result=preview_scope(tmp_path,InputLimits(max_input_bytes=4,max_file_bytes=4),mode='selection',paths=['large'])
    assert result['total_bytes']==5 and result['file_count']==1
    assert {i['code'] for i in result['issues']}=={'input_file_budget','input_byte_budget'}


def test_git_subset_retains_source_policy_and_receipt(tmp_path):
    from naiyue_test_agent.cloud_files.source_scope import plan_scope
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    (tmp_path/'src').mkdir();(tmp_path/'src'/'a.py').write_text('abc')
    (tmp_path/'large').write_text('exclude')
    selected,receipt=plan_scope(tmp_path,InputLimits(),mode='git',paths=['src'])
    assert selected.paths==['src/a.py']
    assert selected.policy.scope==['src']
    assert receipt.root==tmp_path


def test_directory_symlink_cannot_escape(tmp_path):
    from naiyue_test_agent.cloud_files.source_scope import plan_scope
    root=tmp_path/'root';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (outside/'secret').write_text('secret');(root/'escape').symlink_to(outside,target_is_directory=True)
    with pytest.raises(CloudError):plan_scope(root,InputLimits(),mode='selection',paths=['escape/secret'])


def test_large_input_workspace_fits_result_inventory_defaults():
    from naiyue_test_agent.contracts.cloud_results import ResultLimits
    inputs=InputLimits();results=ResultLimits()
    assert results.max_result_bytes>inputs.max_input_bytes
    assert results.max_files>=inputs.max_files
    assert results.max_metadata_bytes>=inputs.max_metadata_bytes


def test_directory_selection_can_exclude_child_before_budget_walk(tmp_path):
    from naiyue_test_agent.cloud_files.source_scope import plan_scope
    (tmp_path/'src').mkdir();(tmp_path/'src'/'keep.py').write_text('ok');(tmp_path/'src'/'large.bin').write_bytes(b'x'*20)
    selected,_=plan_scope(tmp_path,InputLimits(),mode='selection',paths=['src'],exclude=['src/large.bin'])
    assert selected.paths==['src/keep.py']


def test_browse_pins_directory_before_enumeration(tmp_path,monkeypatch):
    import os
    from naiyue_test_agent.cloud_files import source_scope
    root=tmp_path/'root';root.mkdir();chosen=root/'chosen';chosen.mkdir()
    (chosen/'inside.txt').write_text('ok');outside=tmp_path/'outside';outside.mkdir()
    (outside/'private.txt').write_text('secret')
    real=os.scandir
    def swapped(path):
        if os.name=='nt':
            # Pinned Windows directory handles deny rename; POSIX pins the fd instead.
            with pytest.raises(PermissionError):chosen.rename(root/'old')
        else:
            chosen.rename(root/'old');chosen.symlink_to(outside,target_is_directory=True)
        return real(path)
    monkeypatch.setattr(source_scope.os,'scandir',swapped)
    result=source_scope.tree(root,InputLimits(),path='chosen')
    assert [v['name'] for v in result['entries']]==['inside.txt']


@pytest.mark.parametrize('windows',[True,False])
def test_git_scope_and_exclusion_follow_platform_case_rules(tmp_path,monkeypatch,windows):
    import ntpath,posixpath
    from naiyue_test_agent.cloud_files import sources
    from naiyue_test_agent.cloud_files.source_scope import plan_scope
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    (tmp_path/'Src').mkdir();(tmp_path/'Src'/'A.py').write_text('keep');(tmp_path/'Src'/'Skip.py').write_text('exclude')
    # Exercise the platform comparison policy without pretending this host has Win32 handles.
    monkeypatch.setattr(sources,'normcase',ntpath.normcase if windows else posixpath.normcase,raising=False)
    if windows:
        selected,_=plan_scope(tmp_path,InputLimits(),mode='git',paths=['src'],exclude=['src/skip.py'])
        assert selected.paths==['Src/A.py']
    else:
        with pytest.raises(CloudError,match='input_budget'):plan_scope(tmp_path,InputLimits(),mode='git',paths=['src'])
        selected,_=plan_scope(tmp_path,InputLimits(),mode='git',paths=['Src'],exclude=['Src/Skip.py'])
        assert selected.paths==['Src/A.py']
