"""Read actual snapshot bytes without allocating the configured size per file."""
from contextlib import contextmanager
import os
from types import SimpleNamespace

import pytest

from naiyue_test_agent.contracts.git_inputs import GitInputError
from naiyue_test_agent.execution import git_snapshot


def record_reads(monkeypatch, *, before=None, after=None):
    original=git_snapshot.os.fdopen; requests=[]
    @contextmanager
    def opened(fd,*args,**kwargs):
        with original(fd,*args,**kwargs) as stream:
            def read(size):
                requests.append(size)
                if before is not None:before()
                content=stream.read(size)
                return after(content) if after is not None else content
            yield SimpleNamespace(read=read)
    monkeypatch.setattr(git_snapshot.os,'fdopen',opened)
    return requests


@pytest.mark.parametrize('content,limit',[(b'',16777216),(b'small',16777216),
    (b'x'*7,8),(b'x'*8,8)])
def test_read_request_uses_opened_file_size(tmp_path,monkeypatch,content,limit):
    (tmp_path/'input.txt').write_bytes(content)
    requests=record_reads(monkeypatch)
    result,mode,identity=git_snapshot.read_input(tmp_path,'input.txt',limit)
    assert result==content and mode in ('100644','100755')
    assert identity[3]==len(content)
    assert requests==[len(content)+1]


def test_oversized_file_is_rejected_before_read(tmp_path,monkeypatch):
    (tmp_path/'input.txt').write_bytes(b'x'*9)
    requests=record_reads(monkeypatch)
    with pytest.raises(GitInputError,match='git_input_budget'):
        git_snapshot.read_input(tmp_path,'input.txt',8)
    assert requests==[]


@pytest.mark.parametrize('replacement,code',[(b'grown data','git_input_source_changed'),
    (b'x','git_input_source_changed'),(b'x'*33,'git_input_budget')])
def test_file_size_change_during_read_is_rejected(tmp_path,monkeypatch,replacement,code):
    target=tmp_path/'input.txt';target.write_bytes(b'base')
    requests=record_reads(monkeypatch,before=lambda:target.write_bytes(replacement))
    with pytest.raises(GitInputError,match=code):
        git_snapshot.read_input(tmp_path,'input.txt',32)
    assert requests==[5]


@pytest.mark.parametrize('change',[lambda data:data[:-1],lambda data:data+b'!'])
def test_read_length_mismatch_is_rejected_even_with_unchanged_metadata(tmp_path,monkeypatch,change):
    target=tmp_path/'input.txt';target.write_bytes(b'base');initial=target.stat()
    requests=record_reads(monkeypatch,after=change)
    with pytest.raises(GitInputError,match='git_input_source_changed'):
        git_snapshot.read_input(tmp_path,'input.txt',32)
    assert requests==[5]
    assert git_snapshot.file_identity(target.stat())==git_snapshot.file_identity(initial)


def test_same_size_change_after_read_is_rejected(tmp_path,monkeypatch):
    target=tmp_path/'input.txt';target.write_bytes(b'base');initial=target.stat()
    def changed(content):
        target.write_bytes(b'evil')
        os.utime(target,ns=(initial.st_atime_ns,initial.st_mtime_ns+1_000_000_000))
        return content
    requests=record_reads(monkeypatch,after=changed)
    with pytest.raises(GitInputError,match='git_input_source_changed'):
        git_snapshot.read_input(tmp_path,'input.txt',32)
    assert requests==[5]


def test_growth_past_budget_after_read_is_rejected(tmp_path,monkeypatch):
    target=tmp_path/'input.txt';target.write_bytes(b'base')
    def changed(content):
        target.write_bytes(b'x'*33)
        return content
    requests=record_reads(monkeypatch,after=changed)
    with pytest.raises(GitInputError,match='git_input_budget'):
        git_snapshot.read_input(tmp_path,'input.txt',32)
    assert requests==[5]


def test_descriptor_identity_change_after_read_is_rejected(tmp_path,monkeypatch):
    (tmp_path/'input.txt').write_bytes(b'base')
    original=os.fstat;read_finished=False
    def changed(content):
        nonlocal read_finished
        read_finished=True
        return content
    def descriptor_stat(fd):
        info=original(fd)
        if not read_finished:return info
        fields={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
        fields['st_ino']+=1
        return SimpleNamespace(**fields)
    requests=record_reads(monkeypatch,after=changed)
    monkeypatch.setattr(git_snapshot.os,'fstat',descriptor_stat)
    with pytest.raises(GitInputError,match='git_input_source_changed'):
        git_snapshot.read_input(tmp_path,'input.txt',32)
    assert requests==[5]
