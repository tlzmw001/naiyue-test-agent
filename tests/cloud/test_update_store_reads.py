"""Updater readers must tolerate replacement without accepting unsafe handles."""
import errno
import os

import pytest

from naiyue_test_agent.cloud_install import store


def test_read_retries_atomic_replacement_during_handle_validation(tmp_path,monkeypatch,caplog):
    path=tmp_path/'job.json';store.write(path,{'phase':'running'})
    successor=tmp_path/'successor.json';store.write(successor,{'phase':'completed'})
    original=os.fstat;opened=[]
    def replace_once(fd):
        info=original(fd)
        if not opened:
            opened.append(fd);os.replace(successor,path)
        return info
    monkeypatch.setattr(os,'fstat',replace_once)
    if os.name=='nt':
        # Windows pins the opened file against replacement with deny-delete.
        with pytest.raises(PermissionError):store.read(path)
        assert store.read(path)=={'phase':'running'}
        assert 'update_record_read_retry' not in caplog.text
    else:
        assert store.read(path)=={'phase':'completed'}
        assert 'update_record_read_retry' in caplog.text
    with pytest.raises(OSError):original(opened[0])


def test_read_unsafe_handle_retry_is_bounded_and_keeps_error(tmp_path,monkeypatch,caplog):
    path=tmp_path/'job.json';store.write(path,{'phase':'running'})
    error=OSError(errno.EINVAL,'unsafe_store_file');calls=[]
    def unsafe(path):calls.append(path);raise error
    monkeypatch.setattr(store,'open_store_readonly',unsafe)
    with pytest.raises(OSError) as caught:store.read(path)
    assert caught.value is error and len(calls)==3
    assert caplog.text.count('update_record_read_retry')==2


@pytest.mark.parametrize('kind',['symlink','hardlink','permission'])
def test_read_never_accepts_unsafe_replacement(tmp_path,monkeypatch,kind):
    path=tmp_path/'job.json';store.write(path,{'phase':'running'})
    target=tmp_path/'target.json';store.write(target,{'secret':'must not read'})
    original=store.open_store_readonly;calls=[]
    def change(path):
        calls.append(path)
        path.unlink()
        if kind=='symlink':path.symlink_to(target)
        elif kind=='hardlink':os.link(target,path)
        else:raise PermissionError(errno.EACCES,'fixture_denied')
        return original(path)
    monkeypatch.setattr(store,'open_store_readonly',change)
    with pytest.raises(OSError):store.read(path)
    assert len(calls)==1
