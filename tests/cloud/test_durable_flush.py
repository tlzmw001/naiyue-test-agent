"""Windows regular-file flush requires a writable OS handle, including cold I."""
import errno
import os
import sqlite3
import stat
from threading import RLock
from types import SimpleNamespace

import pytest

from test_result_capture_cache import setup_capture, next_plan
from naiyue_test_agent.cloud_runner import result_baseline
from naiyue_test_agent.cloud_runner.result_snapshot import freeze_result, verify_spool
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_results import ResultLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.storage.cloud.operations import backup_database


def checked_flush(monkeypatch, *, fail=False):
    original=os.fsync;seen=[]
    def sync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            # A zero-byte OS write checks actual access, including fdopen('rb')
            # over an O_RDWR descriptor, without altering contents or position.
            os.write(fd,b'');seen.append(os.fstat(fd).st_ino)
            if fail:raise OSError(errno.EIO,'controlled durable flush failure')
        return original(fd)
    monkeypatch.setattr(os,'fsync',sync)
    return seen


@pytest.mark.parametrize('fail',[False,True])
def test_private_baseline_flushes_readonly_git_objects(tmp_path,monkeypatch,fail):
    directory=tmp_path/'cache';objects=directory/'objects.git';objects.mkdir(parents=True)
    target=objects/'object';target.write_bytes(b'git object');target.chmod(0o444)
    seen=checked_flush(monkeypatch,fail=fail)
    if fail:
        with pytest.raises(OSError,match='controlled durable flush failure'):
            result_baseline.cache_files(directory,InputLimits(),durable=True)
    else:
        marker=result_baseline.cache_files(directory,InputLimits(),durable=True)
        assert marker==result_baseline.cache_files(directory,InputLimits())
        assert seen==[target.stat().st_ino]
    assert target.read_bytes()==b'git object'


@pytest.mark.parametrize('cold',[False,True])
def test_cold_and_warm_result_publication_flush_all_files(tmp_path,monkeypatch,cold):
    root,inputs,plan,boundary,limits=setup_capture(tmp_path)
    spools=tmp_path/'spools';result_limits=ResultLimits()
    if not cold:
        freeze_result(root,inputs,spools/'first',plan,boundary,1,result_limits,limits)
        plan=next_plan(plan)
    before={p.name:p.read_bytes() for p in root.iterdir()}
    seen=checked_flush(monkeypatch)
    result=freeze_result(root,inputs,spools/'result',plan,boundary,1,result_limits,limits)
    record=verify_spool(result,plan,boundary,result_limits)
    assert {p.stat().st_ino for p in (result/'files').iterdir()}<=set(seen)
    assert record.bundle is not None
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}


def test_result_flush_error_does_not_publish(tmp_path,monkeypatch):
    root,inputs,plan,boundary,limits=setup_capture(tmp_path);spools=tmp_path/'spools'
    freeze_result(root,inputs,spools/'first',plan,boundary,1,ResultLimits(),limits)
    checked_flush(monkeypatch,fail=True)
    with pytest.raises(CloudError) as caught:
        freeze_result(root,inputs,spools/'failed',next_plan(plan),boundary,1,ResultLimits(),limits)
    assert isinstance(caught.value.__cause__,OSError)
    assert caught.value.__cause__.errno==errno.EIO
    assert not (spools/'failed').exists()


@pytest.mark.parametrize('fail',[False,True])
def test_database_snapshot_flushes_writable_handle(tmp_path,monkeypatch,fail):
    source=sqlite3.connect(':memory:');target=tmp_path/'snapshot.sqlite'
    try:
        source.execute('CREATE TABLE facts(value TEXT)')
        source.execute('INSERT INTO facts VALUES (?)',('retained',));source.commit()
        seen=checked_flush(monkeypatch,fail=fail)
        database=SimpleNamespace(connection=source,lock=RLock())
        if fail:
            with pytest.raises(OSError,match='controlled durable flush failure'):
                backup_database(database,target,guard_format=True)
        else:
            backup_database(database,target,guard_format=True)
            assert seen==[target.stat().st_ino]
            snapshot=sqlite3.connect(target)
            try:
                assert snapshot.execute('SELECT value FROM facts').fetchall()==[('retained',)]
                assert snapshot.execute('PRAGMA user_version').fetchone()==(2,)
            finally:snapshot.close()
    finally:source.close()
