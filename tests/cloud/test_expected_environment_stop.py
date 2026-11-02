"""An owner-confirmed stop is distinct from an unexpected Runner disconnect."""
from uuid import uuid4
import pytest
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CommandReceipt
from naiyue_test_agent.storage.cloud.lifecycle import LifecycleRepository
from test_runner_control import prepared,client,connect,hello
from lifecycle_helpers import config


@pytest.mark.parametrize('phase,kind',[('stopping','stop_environment'),('stopped','stop_environment'),('stopping','refresh')])
def test_expected_environment_stop_does_not_leave_disconnect_blocker(tmp_path,phase,kind):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json();connection=r.control.connections[b.task_id]
            t=r.registry.get_task(b.task_id);ident=str(uuid4())
            candidate=t.model_copy(update=dict(revision=t.revision+1,lifecycle=phase,
                environment_phase='preparing' if phase=='stopping' else 'absent',
                environment_operation_id=ident if phase=='stopping' else None,reclaim_blockers=['unknown']))
            receipt=CommandReceipt(command_id=ident,resource_id=t.task_id,operation='environment:'+kind,
                request_digest='1'*64,status='running',http_status=202,created_at=1.0,updated_at=1.0)
            LifecycleRepository(r.database).reserve(t,candidate,receipt,r.settings)
            c.portal.call(r.control.detach,connection)
            task=r.registry.get_task(b.task_id)
            assert task.runner_connection_id is None
            if kind=='stop_environment':
                assert task.runner_phase=='unconfigured' and task.reclaim_blockers==['unknown']
            else:
                assert task.runner_phase=='disconnected' and task.reclaim_blockers==['unknown','runner_disconnected']
