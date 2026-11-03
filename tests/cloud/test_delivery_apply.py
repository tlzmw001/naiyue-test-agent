"""Raw-byte delivery with persistent progress and conflict-preserving continuation."""
import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from naiyue_test_agent.contracts.cloud_inputs import InputEntry
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def entry(path,raw):
    return InputEntry(path=path,type='file',size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),mode=420)


def output(tmp_path):
    root=tmp_path/'project';root.mkdir();(root/'a').write_bytes(b'old\r\n');(root/'unrelated').write_text('dirty')
    tree=tmp_path/'verified';tree.mkdir();(tree/'a').write_bytes(b'new\r\n');(tree/'b').write_bytes(b'\0binary')
    changes=[dict(path='a',old=entry('a',b'old\r\n'),new=entry('a',b'new\r\n')),
        dict(path='b',old=None,new=entry('b',b'\0binary'))]
    verified=SimpleNamespace(directory=tree,changes=changes,output_manifest=SimpleNamespace(entries=[c['new'] for c in changes]))
    return root,verified


def test_apply_keeps_unrelated_dirty_and_can_reconcile_lost_progress_ack(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_apply import preflight,apply_changes
    root,verified=output(tmp_path);saved=[]
    assert preflight(root,verified)==[]
    def checkpoint(value):
        saved.append(value.copy())
        if value.get('done')==['a']:raise OSError('lost progress ack')
    with pytest.raises(OSError):apply_changes(root,verified,tmp_path/'journal',checkpoint)
    assert (root/'a').read_bytes()==b'new\r\n' and not (root/'b').exists()
    apply_changes(root,verified,tmp_path/'journal',lambda value:saved.append(value.copy()))
    assert (root/'a').read_bytes()==b'new\r\n' and (root/'b').read_bytes()==b'\0binary'
    assert (root/'unrelated').read_text()=='dirty' and saved[-1]['done']==['a','b']


def test_apply_rejects_touched_conflict_and_does_not_clobber_later_user_edit(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_apply import preflight,apply_changes
    root,verified=output(tmp_path);(root/'a').write_text('user')
    assert preflight(root,verified)==['a']
    with pytest.raises(CloudError):apply_changes(root,verified,tmp_path/'journal',lambda value:None)
    assert (root/'a').read_text()=='user' and not (root/'b').exists()


def test_apply_partial_then_concurrent_edit_is_not_overwritten(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_apply import apply_changes
    root,verified=output(tmp_path)
    def fail(value):
        if value.get('done')==['a']:raise OSError('disk unavailable')
    with pytest.raises(OSError):apply_changes(root,verified,tmp_path/'journal',fail)
    (root/'a').write_text('new user edit')
    with pytest.raises(CloudError):apply_changes(root,verified,tmp_path/'journal',lambda value:None)
    assert (root/'a').read_text()=='new user edit' and not (root/'b').exists()


def test_parent_link_swap_and_export_existing_directory_are_rejected(tmp_path):
    from naiyue_test_agent.cloud_files.delivery_apply import preflight,export_tree
    root,verified=output(tmp_path);outside=tmp_path/'outside';outside.mkdir();(root/'linked').symlink_to(outside,target_is_directory=True)
    verified.changes=[dict(path='linked/pwn',old=None,new=entry('linked/pwn',b'x'))]
    assert preflight(root,verified)==['linked/pwn']
    (root/'destination').mkdir()
    with pytest.raises(CloudError):export_tree(root,'destination',verified,tmp_path/'export-journal')
    assert list((root/'destination').iterdir())==[] and list(outside.iterdir())==[]


def test_export_reconciles_publication_before_receipt_without_overwriting(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files import delivery_apply as module
    root,verified=output(tmp_path);original=module.write_json
    def failed_marker(path,value):
        if value.get('phase')=='completed':raise OSError('receipt lost')
        return original(path,value)
    monkeypatch.setattr(module,'write_json',failed_marker)
    with pytest.raises(OSError):module.export_tree(root,'exported',verified,tmp_path/'export-journal')
    assert (root/'exported/a').read_bytes()==b'new\r\n'
    monkeypatch.setattr(module,'write_json',original)
    assert module.export_tree(root,'exported',verified,tmp_path/'export-journal')==dict(name='exported')
    assert (root/'exported/b').read_bytes()==b'\0binary'


def test_interrupted_backup_can_continue_without_any_project_effect(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files import delivery_apply as module
    root,verified=output(tmp_path);original=module.write_json;failed=False
    def fault(path,value):
        nonlocal failed
        if path.name=='operation.json' and not failed:
            failed=True;raise OSError('intent sync unavailable')
        return original(path,value)
    monkeypatch.setattr(module,'write_json',fault)
    with pytest.raises(OSError):module.apply_changes(root,verified,tmp_path/'journal',lambda v:None)
    assert (root/'a').read_bytes()==b'old\r\n' and not (root/'b').exists()
    monkeypatch.setattr(module,'write_json',original)
    module.apply_changes(root,verified,tmp_path/'journal',lambda v:None)
    assert (root/'a').read_bytes()==b'new\r\n'


@pytest.mark.parametrize('path',['a','b'])
def test_user_write_while_temporary_file_is_being_prepared_is_preserved(tmp_path,monkeypatch,path):
    from naiyue_test_agent.cloud_files import delivery_apply as module
    root,verified=output(tmp_path);original=module.replace_entry
    def competing(fd,directory,name,source,entry,**kwargs):
        if name==path:(directory/name).write_bytes(b'user concurrent edit')
        return original(fd,directory,name,source,entry,**kwargs)
    monkeypatch.setattr(module,'replace_entry',competing)
    with pytest.raises(CloudError):module.apply_changes(root,verified,tmp_path/'journal',lambda v:None)
    assert (root/path).read_bytes()==b'user concurrent edit'


def test_export_retry_reuses_its_original_staging_after_copy_ack_loss(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files import delivery_apply as module
    root,verified=output(tmp_path);original=module.shutil.copytree
    def lost_ack(*args,**kwargs):
        original(*args,**kwargs);raise OSError('copy ack lost')
    monkeypatch.setattr(module.shutil,'copytree',lost_ack)
    with pytest.raises(OSError):module.export_tree(root,'exported',verified,tmp_path/'export-journal')
    stages=list(root.glob('.nyt-export-*'));assert len(stages)==1
    monkeypatch.setattr(module.shutil,'copytree',original)
    module.export_tree(root,'exported',verified,tmp_path/'export-journal')
    assert list(root.glob('.nyt-export-*'))==[]
    assert (root/'exported/a').read_bytes()==b'new\r\n'


@pytest.mark.parametrize('prefix',[b'ne',b'user edit'])
def test_partial_export_continues_only_fixed_output_prefix_without_overwriting(tmp_path,monkeypatch,prefix):
    from naiyue_test_agent.cloud_files import delivery_apply as module
    root,verified=output(tmp_path);original=module.shutil.copytree
    def interrupted(source,destination,**kwargs):
        (destination/'a').write_bytes(prefix);raise OSError('copy interrupted')
    monkeypatch.setattr(module.shutil,'copytree',interrupted)
    with pytest.raises(OSError):module.export_tree(root,'exported',verified,tmp_path/'journal')
    stage,=root.glob('.nyt-export-*')
    monkeypatch.setattr(module.shutil,'copytree',original)
    if prefix==b'ne':
        module.export_tree(root,'exported',verified,tmp_path/'journal')
        assert (root/'exported/a').read_bytes()==b'new\r\n' and (root/'exported/b').read_bytes()==b'\0binary'
        assert list(root.glob('.nyt-export-*'))==[]
    else:
        with pytest.raises(CloudError):module.export_tree(root,'exported',verified,tmp_path/'journal')
        assert (stage/'a').read_bytes()==prefix and not (root/'exported').exists()


def test_output_closure_does_not_ignore_an_unclassified_filesystem_entry(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_files import delivery_apply as module
    _,verified=output(tmp_path);special=verified.directory/'pipe';special.write_bytes(b'')
    # Model an entry (such as a FIFO) which convenience is_file/is_symlink
    # predicates do not classify. Closed inventory must still account for it.
    original=Path.is_file
    monkeypatch.setattr(Path,'is_file',lambda path:False if path==special else original(path))
    assert module._matches_output(verified.directory,verified) is False
