import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from naiyue_test_agent.contracts.cloud_inputs import Selection, InputLimits, InputManifest
from naiyue_test_agent.cloud_files.capture import capture
from naiyue_test_agent.cloud_files.archive import validate_package
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def package(tmp_path):
    source = tmp_path/'source';source.mkdir()
    (source/'test_demo.py').write_bytes(b'def test_ok():\r\n    assert True\r\n')
    (source/'api.proto').write_bytes(b'syntax = "proto3";\n')
    (source/'blob.bin').write_bytes(bytes(range(256))*8)
    selection = Selection(paths=['api.proto','blob.bin','test_demo.py'])
    result = capture(source, selection, tmp_path/'capture', InputLimits())
    return result, source, selection


def test_capture_identity_preserves_bytes_and_archive_validates(tmp_path):
    result, source, selection = package(tmp_path)
    manifest, policy = validate_package(result.directory/'archive', result.codec, InputLimits(), expected_id=result.input_id)
    assert manifest.input_id == result.input_id
    assert hashlib.sha256((result.directory/'archive').read_bytes()).hexdigest() == result.archive_sha256
    assert policy == selection
    assert {e.path:e.sha256 for e in manifest.entries} == {
        f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in source.iterdir()}
    assert not (source/'.git').exists()
    again = capture(source, selection, tmp_path/'again', InputLimits(), codec='tar')
    assert again.input_id == result.input_id and again.archive_sha256 != result.archive_sha256


@pytest.mark.parametrize('mode',['selection','git'])
@pytest.mark.parametrize('codec',['gzip','tar'])
def test_capture_durability_uses_writable_handles_without_changing_source(tmp_path,monkeypatch,mode,codec):
    import errno,os
    from pathlib import Path
    from test_git_source_plan import repository
    from naiyue_test_agent.cloud_files.sources import select_git
    source=repository(tmp_path)
    before={p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}
    plan=select_git(source,InputLimits()) if mode=='git' else None
    selection=plan.selection if plan else Selection(paths=['api.proto','readme.txt'])
    handles={};synced=set();original_open=Path.open;original_sync=os.fsync
    def opened(path,*args,**kwargs):
        stream=original_open(path,*args,**kwargs);handles[stream.fileno()]=(path,stream);return stream
    def windows_sync(fd):
        item=handles.get(fd)
        if item and not item[1].closed:
            if not item[1].writable():raise OSError(errno.EBADF,'read-only Windows flush handle')
            if item[0].parent.name=='result':synced.add(item[0].name)
        original_sync(fd)
    monkeypatch.setattr(Path,'open',opened);monkeypatch.setattr(os,'fsync',windows_sync)
    result=capture(source,selection,tmp_path/'capture',InputLimits(),codec=codec,source_receipt=plan.receipt if plan else None)
    expected={'archive','manifest.json','selection.json','input.json','receipt.json'}|({'source.json'} if plan else set())
    assert synced==expected
    assert {p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}==before
    assert validate_package(result.directory/'archive',codec,InputLimits(),expected_id=result.input_id)[0].input_id==result.input_id


def test_capture_flush_failure_never_publishes_input(tmp_path,monkeypatch,caplog):
    import errno,os
    from pathlib import Path
    source=tmp_path/'source';source.mkdir();(source/'a').write_bytes(b'unchanged')
    original_open=Path.open;original_sync=os.fsync;handles={}
    def opened(path,*args,**kwargs):
        stream=original_open(path,*args,**kwargs);handles[stream.fileno()]=(path,stream);return stream
    def failed_sync(fd):
        item=handles.get(fd)
        if item and not item[1].closed and item[0].parent.name=='result':raise OSError(errno.EIO,'controlled flush failure')
        original_sync(fd)
    monkeypatch.setattr(Path,'open',opened);monkeypatch.setattr(os,'fsync',failed_sync)
    with pytest.raises(CloudError,match='input_source_unavailable'):
        capture(source,Selection(paths=['a']),tmp_path/'capture',InputLimits())
    assert not (tmp_path/'capture').exists() and (source/'a').read_bytes()==b'unchanged'
    assert 'cloud_input_capture_failed' in caplog.text


@pytest.mark.parametrize('path', ['../escape','/abs','AUX.txt','x:y','a\\b','a.','a//b','.git/config','.env','a?b','a*b','a"b','a<b','a>b','a|b'])
def test_selection_rejects_nonportable_or_excluded_paths(path):
    with pytest.raises(ValueError): Selection(paths=[path])


def test_collision_and_file_parent_are_rejected():
    for paths in (['Foo','foo'],['a','a/b'],['é','e\u0301']):
        with pytest.raises(ValueError): Selection(paths=paths)


def test_executable_crlf_fails_without_changing_source(tmp_path):
    source=tmp_path/'source';source.mkdir();script=source/'run.sh';raw=b'#!/bin/sh\r\necho ok\r\n';script.write_bytes(raw)
    with pytest.raises(CloudError,match='input_crlf_executable'):
        capture(source,Selection(paths=['run.sh'],executables=['run.sh']),tmp_path/'capture',InputLimits())
    assert script.read_bytes()==raw and not (tmp_path/'capture').exists()


def test_extra_tar_member_and_hardlink_cannot_be_committed(tmp_path):
    result,_,_=package(tmp_path)
    original=result.directory/'archive'
    for kind in (tarfile.REGTYPE,tarfile.LNKTYPE):
        path=tmp_path/('bad-'+kind.decode())
        with tarfile.open(original,'r:gz') as source,tarfile.open(path,'w') as target:
            for info in source:
                target.addfile(info,source.extractfile(info) if info.isfile() else None)
            member=tarfile.TarInfo('data/extra');member.type=kind;member.size=1 if kind==tarfile.REGTYPE else 0
            member.linkname='data/api.proto' if kind==tarfile.LNKTYPE else ''
            target.addfile(member,io.BytesIO(b'x') if member.size else None)
        with pytest.raises(CloudError): validate_package(path,'tar',InputLimits(),expected_id=result.input_id)


def test_safe_relative_link_and_source_hardlink_are_independent_bytes(tmp_path):
    import os
    root=tmp_path/'source';root.mkdir();(root/'a').write_bytes(b'content');os.link(root/'a',root/'b');(root/'link').symlink_to('a')
    result=capture(root,Selection(paths=['a','b','link']),tmp_path/'capture',InputLimits())
    manifest,_=validate_package(result.directory/'archive','gzip',InputLimits())
    assert [e.type for e in manifest.entries]==['file','file','symlink']
    (root/'outside').symlink_to('../secret')
    with pytest.raises((ValueError,CloudError)):
        capture(root,Selection(paths=['outside']),tmp_path/'bad',InputLimits())


@pytest.mark.parametrize('name',['.netrc','_netrc','id_dsa','id_ecdsa','certificate.p12','certificate.pfx'])
def test_known_credential_filenames_are_excluded(name):
    with pytest.raises(ValueError):Selection(paths=[name])


def test_known_private_key_content_is_rejected_without_publishing(tmp_path):
    source=tmp_path/'source';source.mkdir()
    # Synthetic marker only; no real credential data is used.
    (source/'notes.txt').write_bytes(b'prefix\n-----BEGIN PRIVATE KEY-----\nsynthetic fixture\n')
    with pytest.raises(CloudError,match='input_sensitive'):
        capture(source,Selection(paths=['notes.txt']),tmp_path/'capture',InputLimits())
    assert not (tmp_path/'capture').exists()


def test_source_change_during_capture_is_not_published(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import importlib
    module=importlib.import_module('naiyue_test_agent.cloud_files.capture');original=module.source_handle
    root=tmp_path/'source';root.mkdir();(root/'a').write_bytes(b'original')
    @contextmanager
    def changing(*args):
        with original(*args) as values:
            yield values
            (root/'a').write_bytes(b'changed!')
    monkeypatch.setattr(module,'source_handle',changing)
    with pytest.raises(CloudError,match='input_source_changed'):
        capture(root,Selection(paths=['a']),tmp_path/'capture',InputLimits())
    assert not (tmp_path/'capture').exists()


def test_unicode_pax_and_trailing_data_are_bounded(tmp_path):
    root=tmp_path/'source';root.mkdir();(root/'测试.py').write_bytes(b'assert True\n')
    result=capture(root,Selection(paths=['测试.py']),tmp_path/'capture',InputLimits(),codec='tar')
    assert validate_package(result.directory/'archive','tar',InputLimits())[0].input_id==result.input_id
    path=tmp_path/'trailing';path.write_bytes((result.directory/'archive').read_bytes()+b'hidden data')
    with pytest.raises(CloudError):validate_package(path,'tar',InputLimits())
    oversized=tmp_path/'oversized'
    with oversized.open('wb') as stream:
        header=tarfile.TarInfo('pax');header.type=tarfile.XHDTYPE;header.size=8192;stream.write(header.tobuf())
    with pytest.raises(CloudError,match='input_tar_metadata_budget'):validate_package(oversized,'tar',InputLimits())


def test_archive_transport_hash_is_verified_separately_from_input_identity(tmp_path):
    result,_,_=package(tmp_path)
    with pytest.raises(CloudError,match='input_archive_hash_mismatch'):
        validate_package(result.directory/'archive',result.codec,InputLimits(),expected_id=result.input_id,
            expected_archive_sha256='0'*64,expected_size=result.size)


@pytest.mark.parametrize('io_bytes',[1,4])
def test_lfs_pointer_is_refused_even_across_small_read_chunks(tmp_path,io_bytes):
    root=tmp_path/'source';root.mkdir();(root/'asset.bin').write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:fixture\n')
    with pytest.raises(CloudError,match='input_lfs_pointer_unsupported'):
        capture(root,Selection(paths=['asset.bin']),tmp_path/'capture',InputLimits(io_bytes=io_bytes))
