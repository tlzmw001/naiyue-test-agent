"""Stopped-task backups copy raw bytes, never parse task SQLite."""
import hashlib,json,os
from uuid import uuid4

import pytest
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def identity():return dict(command_id=str(uuid4()),task_id='a'*32,generation=2,reservation_sha256='b'*64)


def test_raw_backup_preserves_wal_bytes_and_has_durable_verifiable_completion(tmp_path):
    from naiyue_test_agent.cloud_runner.upgrade_backup import backup
    workspace=tmp_path/'workspace';state=tmp_path/'state';dest=tmp_path/'backup'
    workspace.mkdir();state.mkdir();(workspace/'file.txt').write_bytes(b'user\0bytes');(state/'session.sqlite').write_bytes(b'not parsed as SQLite');(state/'session.sqlite-wal').write_bytes(b'wal bytes')
    request=identity();result=backup(workspace,state,dest,request,max_bytes=10000,max_files=100)
    assert result['files']==3 and result['bytes']==len(b'user\0bytesnot parsed as SQLitewal bytes')
    complete=json.loads((dest/'complete.json').read_text());manifest=dest/complete['payload']/'manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==result['manifest_sha256']
    assert (manifest.parent/'state/session.sqlite-wal').read_bytes()==b'wal bytes'
    assert backup(workspace,state,dest,request,max_bytes=10000,max_files=100)==result
    (manifest.parent/'state/session.sqlite-wal').write_bytes(b'corrupted')
    with pytest.raises(CloudError,match='upgrade_backup_invalid'):backup(workspace,state,dest,request,max_bytes=10000,max_files=100)


@pytest.mark.parametrize('kind',['symlink','hardlink','fifo','budget'])
def test_unsafe_or_over_budget_source_never_publishes_complete(tmp_path,kind):
    from naiyue_test_agent.cloud_runner.upgrade_backup import backup
    workspace=tmp_path/'workspace';state=tmp_path/'state';dest=tmp_path/'backup';workspace.mkdir();state.mkdir();(workspace/'file').write_bytes(b'abcdef')
    if kind=='symlink':(state/'bad').symlink_to(workspace/'file')
    if kind=='hardlink':os.link(workspace/'file',state/'bad')
    if kind=='fifo':os.mkfifo(state/'bad')
    with pytest.raises((CloudError,OSError)):
        backup(workspace,state,dest,identity(),max_bytes=2 if kind=='budget' else 10000,max_files=100)
    assert not (dest/'complete.json').exists()


def test_change_to_an_earlier_file_before_final_publication_is_rejected(tmp_path,monkeypatch):
    import naiyue_test_agent.cloud_runner.upgrade_backup as module
    workspace=tmp_path/'workspace';state=tmp_path/'state';destination=tmp_path/'backup';workspace.mkdir();state.mkdir()
    first=workspace/'first';first.write_bytes(b'original');last=state/'last';last.write_bytes(b'state')
    inode=last.stat().st_ino;original=os.read;changed=[]
    def mutate(fd,size):
        if os.fstat(fd).st_ino==inode and not changed:first.write_bytes(b'new local bytes');changed.append(True)
        return original(fd,size)
    monkeypatch.setattr(module.os,'read',mutate)
    with pytest.raises(CloudError,match='upgrade_backup_source_changed'):
        module.backup(workspace,state,destination,identity(),max_bytes=10000,max_files=100)
    assert not (destination/'complete.json').exists()
