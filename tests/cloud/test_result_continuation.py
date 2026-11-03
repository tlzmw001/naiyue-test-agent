"""Pending results never make a useful managed background service unusable."""
from uuid import uuid4
import pytest
from cloud_helpers import owner_headers
from lifecycle_helpers import config
from test_runner_control import prepared,client,connect,hello
from test_results import finish,run_body
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError


@pytest.mark.parametrize('quiet',[False,True])
def test_next_message_atomically_supersedes_only_uncaptured_pending_result(tmp_path,quiet):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json()
            previous,_=finish(r,b,c,ws,run_body(r.registry.get_task(b.task_id)),quiet=quiet)
            rid=previous['result_plan']['result_id'];row=r.registry.get_task(b.task_id)
            body=run_body(row)
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=body)
            assert response.status_code==202,response.text
            command=ws.receive_json()['command'];old=r.results.get(b.task_id,rid)
            assert old.phase=='superseded' and old.superseded_by==command['run_id'] and old.bundle is None
            current=r.registry.get_task(b.task_id)
            assert rid not in current.result_ids and current.active_run_id==command['run_id']
            assert current.pending_result_id is None and 'result_pending' not in current.reclaim_blockers
            assert previous['run_id']!=command['run_id']
            replay=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=body)
            assert replay.json()==response.json() and r.results.get(b.task_id,rid)==old
            with pytest.raises(CloudError):r.results.begin(b.token.get_secret_value(),b.task_id,rid)


def test_failed_new_admission_does_not_abandon_previous_result(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json();previous,_=finish(r,b,c,ws,run_body(r.registry.get_task(b.task_id)),quiet=False)
            rid=previous['result_plan']['result_id'];before=r.results.get(b.task_id,rid)
            with r.database.transaction() as db:count=db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]
            r.settings=r.settings.model_copy(update={'max_receipts':count+1})
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=run_body(r.registry.get_task(b.task_id)))
            assert response.status_code==409 and response.json()['error']['code']=='command_capacity'
            assert r.results.get(b.task_id,rid)==before and r.registry.get_task(b.task_id).pending_result_id==rid


def test_begin_wins_exclusion_and_capture_must_finish_before_next_message(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            ws.send_json(hello(b));ws.receive_json();previous,_=finish(r,b,c,ws,run_body(r.registry.get_task(b.task_id)))
            rid=previous['result_plan']['result_id'];capturing=r.results.begin(b.token.get_secret_value(),b.task_id,rid)
            assert capturing.phase=='capturing'
            response=c.post('/api/cloud/v1/tasks/'+b.task_id+'/runs',headers=owner_headers(r),json=run_body(r.registry.get_task(b.task_id)))
            assert response.status_code==409 and r.results.get(b.task_id,rid)==capturing
