"""Shared raw tree operations never follow links or open task databases."""
import os
import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_raw_tree_roundtrip_checks_bytes_and_restores_executable_mode(tmp_path):
    from naiyue_test_agent.storage.raw_tree import copy_roots,verify_tree,restore_modes
    source=tmp_path/'source';source.mkdir();(source/'run.sh').write_bytes(b'echo fixture');(source/'run.sh').chmod(0o755)
    (source/'session.sqlite-wal').write_bytes(b'opaque WAL');target=tmp_path/'target';target.mkdir()
    entries=copy_roots([('state',source,target)],max_bytes=1000,max_files=20)
    verify_tree({'state':target},entries,max_bytes=1000,max_files=20)
    restore_modes({'state':target},entries)
    assert (target/'run.sh').stat().st_mode&0o777==0o755
    (target/'session.sqlite-wal').write_bytes(b'changed')
    with pytest.raises(CloudError):verify_tree({'state':target},entries,max_bytes=1000,max_files=20)


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo'])
def test_managed_removal_refuses_unsafe_entries_and_preserves_outside(tmp_path,kind):
    from naiyue_test_agent.storage.raw_tree import remove_tree
    source=tmp_path/'owned';source.mkdir();outside=tmp_path/'outside';outside.write_bytes(b'keep')
    if kind=='symlink':(source/'bad').symlink_to(outside)
    elif kind=='hardlink':os.link(outside,source/'bad')
    else:os.mkfifo(source/'bad')
    with pytest.raises((CloudError,OSError)):remove_tree(source,max_files=100)
    assert outside.read_bytes()==b'keep'


def test_confirmed_inventory_rejects_a_file_added_before_the_delete_walk(tmp_path):
    from naiyue_test_agent.storage.raw_tree import remove_tree,tree_inventory
    source=tmp_path/'owned';source.mkdir();(source/'original').write_bytes(b'old')
    expected=[source.stat().st_dev,source.stat().st_ino];inventory=tree_inventory(source,max_files=20)
    (source/'new').write_bytes(b'new user bytes')
    with pytest.raises(CloudError,match='managed_tree_changed'):remove_tree(source,max_files=20,expected=expected,expected_inventory=inventory)
    assert (source/'new').read_bytes()==b'new user bytes'


def test_root_identity_is_compared_with_the_actual_opened_fd(tmp_path,monkeypatch):
    import naiyue_test_agent.storage.raw_tree as module
    source=tmp_path/'owned';source.mkdir();(source/'old').write_bytes(b'old')
    expected=[source.stat().st_dev,source.stat().st_ino];original=module.os.stat;changed=[]
    def swap(name,*a,**kw):
        if name=='owned' and 'dir_fd' in kw and not changed:
            source.rename(tmp_path/'retired');source.mkdir();(source/'keep').write_bytes(b'new root');changed.append(True)
        return original(name,*a,**kw)
    monkeypatch.setattr(module.os,'stat',swap)
    with pytest.raises(CloudError,match='managed_tree_changed'):module.remove_tree(source,max_files=20,expected=expected)
    assert (source/'keep').read_bytes()==b'new root'
