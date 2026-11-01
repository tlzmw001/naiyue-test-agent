"""An access-denied retry is limited to the pinned private single-link file."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from naiyue_test_agent.application.environments import inputs
from naiyue_test_agent.storage import files


def setup(tmp_path, monkeypatch, *, retry_fails=False, readonly=True):
    directory=tmp_path/'git-inputs'/'input'
    directory.mkdir(parents=True); path=directory/'object';path.write_bytes(b'git-object')
    service=object.__new__(inputs.InputService);service.directory=lambda *_:directory
    error=PermissionError(13,'PRIVATE_PATH');error.winerror=5
    original=inputs.shutil.rmtree; cleared=[]
    def clear(value):
        cleared.append(Path(value))
        if retry_fails:raise PermissionError(13,'PRIVATE_ACL')
        if not readonly:return None
        info=Path(value).lstat();return info.st_dev,info.st_ino
    def remove(value,*,onexc):
        onexc(os.unlink,path,error)
        original(value)
    monkeypatch.setattr(inputs.shutil,'rmtree',remove)
    monkeypatch.setattr(inputs,'clear_store_readonly',clear,raising=False)
    return service,directory,path,error,cleared


def test_readonly_retry_removes_only_verified_private_file(tmp_path,monkeypatch):
    service,directory,path,_,cleared=setup(tmp_path,monkeypatch)
    service._remove_directory('P','a'*32)
    assert cleared==[path] and not directory.exists()


@pytest.mark.parametrize('kind',['hardlink','symlink','outside'])
def test_untrusted_file_is_never_made_writable(tmp_path,monkeypatch,kind):
    service,directory,path,error,cleared=setup(tmp_path,monkeypatch)
    protected=tmp_path/'protected';protected.write_bytes(b'keep')
    if kind=='hardlink':path.unlink();os.link(protected,path)
    elif kind=='symlink':path.unlink();path.symlink_to(protected)
    else:
        def remove(value,*,onexc):onexc(os.unlink,protected,error)
        monkeypatch.setattr(inputs.shutil,'rmtree',remove)
    with pytest.raises(OSError):service._remove_directory('P','a'*32)
    assert not cleared and protected.read_bytes()==b'keep'


@pytest.mark.parametrize('readonly',[True,False])
def test_failed_or_inapplicable_retry_preserves_original_and_does_not_loop(tmp_path,monkeypatch,caplog,readonly):
    service,_,path,error,cleared=setup(tmp_path,monkeypatch,retry_fails=readonly,readonly=readonly)
    with pytest.raises(PermissionError) as caught:service._remove_directory('P','a'*32)
    assert caught.value is error and cleared==[path] and path.exists()
    assert 'git_input_directory_cleanup_failed' in caplog.text and 'PRIVATE' not in caplog.text


def test_file_changed_after_attribute_update_is_not_deleted(tmp_path,monkeypatch):
    service,_,path,_,cleared=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(inputs,'clear_store_readonly',lambda _:('different','identity'))
    with pytest.raises(OSError):service._remove_directory('P','a'*32)
    assert path.exists() and not cleared


def test_real_git_readonly_objects_retire_through_existing_entry(tmp_path,monkeypatch):
    # Native Windows runs this same test: a Git snapshot contains read-only loose objects.
    import subprocess
    from threading import RLock
    directory=tmp_path/'git-inputs'/'input';directory.mkdir(parents=True)
    subprocess.run(['git','init','--bare','-q',str(directory/'objects.git')],check=True)
    subprocess.run(['git','--git-dir',str(directory/'objects.git'),'hash-object','-w','--stdin'],
        input=b'object',stdout=subprocess.PIPE,check=True)
    for file in (directory/'objects.git'/'objects').glob('*/*'):file.chmod(0o444)
    deleted=[];service=object.__new__(inputs.InputService)
    service.directory=lambda *_:directory;service._lock=lambda _:RLock()
    service.records=lambda:SimpleNamespace(get=lambda *_:SimpleNamespace(state='ready'),
        can_retire=lambda *_:True,delete_unreferenced=lambda *args:deleted.append(args) or True)
    assert service.retire('P','a'*32,None) is True
    assert deleted==[('P','a'*32)] and not directory.exists()
