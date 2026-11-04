"""Recovery and deleting guards are re-read only after the root OS lock is held."""
from uuid import uuid4
import pytest
from lifecycle_helpers import config
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('name,phase,code',[('restore-guard.json','prepared','cloud_restore_guard'),
    ('restore-guard.json','superseded','cloud_restore_guard'),('gc-guard.json','deleting','cloud_gc_guard')])
def test_service_never_opens_a_guarded_root(tmp_path,name,phase,code):
    from naiyue_test_agent.cloud_install.store import write
    cloud=config(tmp_path)
    with open_cloud_runtime(cloud):pass
    write(cloud.data_dir/name,dict(phase=phase,command_id=str(uuid4())))
    with pytest.raises(CloudError,match=code):
        with open_cloud_runtime(cloud):pytest.fail('guard ignored')


def test_guard_written_between_marker_read_and_owner_lock_still_blocks_startup(tmp_path,monkeypatch):
    from naiyue_test_agent.cloud_install.store import write
    import naiyue_test_agent.application.cloud.ownership as ownership
    cloud=config(tmp_path)
    with open_cloud_runtime(cloud):pass
    original=ownership.acquire_store_lock;called=[]
    def lock(fd):
        if not called:
            write(cloud.data_dir/'restore-guard.json',dict(phase='superseded',command_id=str(uuid4())));called.append(True)
        return original(fd)
    monkeypatch.setattr(ownership,'acquire_store_lock',lock)
    with pytest.raises(CloudError,match='cloud_restore_guard'):
        with open_cloud_runtime(cloud):pytest.fail('stale permission accepted')
    assert called


def test_guard_capable_format_is_explicit_and_reopens_without_changing_task_schema(tmp_path):
    import json,sqlite3
    from naiyue_test_agent.storage.cloud.operations import OperationRepository
    cloud=config(tmp_path)
    with open_cloud_runtime(cloud) as r:
        OperationRepository(r.database).enable_guards();r.owner.enable_guards()
        assert r.database.connection.execute('PRAGMA user_version').fetchone()[0]==2
    assert json.loads((cloud.data_dir/'cloud-root.json').read_text())['schema']==2
    with open_cloud_runtime(cloud) as r:assert r.owner.record['schema']==2
