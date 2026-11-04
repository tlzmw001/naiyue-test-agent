from uuid import uuid4
import hashlib,pytest
from lifecycle_helpers import config,register
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError,CommandReceipt
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository


def test_pending_transfer_is_draining_and_original_can_be_cancelled(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        transfer=r.inputs.create(dict(command_id=str(uuid4()),purpose='input',input_id='a'*64,size=3,archive_sha256=hashlib.sha256(b'abc').hexdigest(),codec='tar'))
        ident=transfer.resource_id
        assert r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))['maintenance']=='draining'
        with pytest.raises(CloudError,match='cloud_maintenance'):
            r.inputs.create(dict(command_id=str(uuid4()),purpose='input',input_id='b'*64,size=3,archive_sha256='b'*64,codec='tar'))
        r.inputs.cancel(ident)
        assert r.releases.maintenance_status()['maintenance']=='ready'


def test_registered_idle_task_without_producer_does_not_prevent_maintenance(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        register(r)
        assert r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))['maintenance']=='ready'


@pytest.mark.parametrize('operation',['run:start','environment:start','artifact:capture'])
def test_new_producers_check_maintenance_in_the_reservation_transaction(tmp_path,operation):
    with open_cloud_runtime(config(tmp_path)) as r:
        task=register(r);r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))
        receipt=CommandReceipt(command_id=str(uuid4()),resource_id=task.task_id,operation=operation,request_digest='a'*64,
            status='accepted',http_status=202,result={},created_at=1.0,updated_at=1.0)
        with pytest.raises(CloudError,match='cloud_maintenance'):
            LifecycleRepository(r.database).reserve(task,task.model_copy(update={'revision':task.revision+1}),receipt,r.settings)
        assert r.registry.get_task(task.task_id)==task


def test_result_cleanup_is_fenced_and_existing_deleting_work_can_drain(tmp_path,monkeypatch):
    from test_runner_control import prepared,client,connect
    from test_result_retention import available,newer
    import naiyue_test_agent.application.cloud.result_retention as module
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,_=available(r,b,c,ws,tmp_path);newer(r,b)
            observed=[];original=module.remove_task_data
            def check(*args,**kwargs):
                observed.append(r.releases.maintenance_status())
                return original(*args,**kwargs)
            monkeypatch.setattr(module,'remove_task_data',check)
            r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))
            r.results.retention.collect(b.task_id)
            assert observed==[] and r.results.get(b.task_id,rid).phase=='available'
            r.releases.maintenance(dict(command_id=str(uuid4()),action='leave'))
            def fail(*args,**kwargs):raise OSError('controlled cleanup failure')
            monkeypatch.setattr(module,'remove_task_data',fail)
            r.results.retention.collect(b.task_id)
            assert r.results.get(b.task_id,rid).phase=='deleting'
            assert r.releases.maintenance(dict(command_id=str(uuid4()),action='enter'))['maintenance']=='draining'
            monkeypatch.setattr(module,'remove_task_data',check)
            r.results.retention.collect(b.task_id)
            assert r.results.get(b.task_id,rid).phase=='deleted'
            assert observed and all(item['maintenance']=='draining' for item in observed)
