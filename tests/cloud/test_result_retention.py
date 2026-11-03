"""Retained result quotas protect leases/downloads and release bytes only after cleanup."""
import asyncio
from uuid import uuid4
import pytest
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from lifecycle_helpers import config
from cloud_helpers import owner_headers
from test_runner_control import prepared,client,connect
from test_result_api import frozen,transfer


def available(r,b,c,ws,path):
    rid,raw,spool,state=frozen(r,b,c,ws,path)
    r.results.prepare(raw,b.task_id,rid,state.bundle.model_dump(mode='json'))
    transfer(r,b,c,spool,state)
    r.results.commit(raw,b.task_id,rid)
    return rid,state


def newer(r,b):
    task=r.registry.get_task(b.task_id)
    plan,receipt=r.results.reserve_run(task,str(uuid4()),uuid4().hex)
    receipt=receipt.model_copy(update={'result':receipt.result|{'phase':'available'},'status':'succeeded'})
    candidate=task.model_copy(update={'revision':task.revision+1,'result_ids':[*task.result_ids,plan.result_id]})
    r.results.repository.reserve(task,candidate,receipt,r.settings)
    return plan.result_id


def test_receive_lease_auth_and_expiry_protect_old_result(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,state=available(r,b,c,ws,tmp_path);lease=str(uuid4())
            path='/api/cloud/v1/tasks/'+b.task_id+'/results/'+rid
            assert c.post(path+'/lease',json={'lease_id':lease}).status_code==401
            response=c.post(path+'/lease',headers=owner_headers(r),json={'lease_id':lease})
            assert response.status_code==200,response.text
            assert response.json()['result_id']==rid and response.json()['lease_id']==lease
            newer(r,b);r.results.retention.collect(b.task_id)
            assert r.results.get(b.task_id,rid).phase=='available'
            assert r.results.get(b.task_id,rid).bundle==state.bundle
            r.results.retention.collect(b.task_id,now=response.json()['expires_at']+1)
            old=r.results.get(b.task_id,rid)
            assert old.phase=='deleted' and old.retired_phase=='available' and old.bundle is None
            assert rid not in r.registry.get_task(b.task_id).result_ids
            for item in state.bundle.files:
                with pytest.raises(CloudError,match='artifact_not_found'):r.artifacts.repository.get(b.task_id,item.artifact.artifact_id)


def test_cleanup_failure_is_durable_and_does_not_free_quota(tmp_path,monkeypatch):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,state=available(r,b,c,ws,tmp_path);newer(r,b)
            import naiyue_test_agent.application.cloud.result_retention as module
            original=module.remove_task_data
            def fail(*args,**kwargs):raise OSError('fixture')
            monkeypatch.setattr(module,'remove_task_data',fail)
            r.results.retention.collect(b.task_id)
            assert r.results.get(b.task_id,rid).phase=='deleting'
            assert rid in r.registry.get_task(b.task_id).result_ids
            a=state.bundle.files[0].artifact
            assert r.artifacts.repository.get(b.task_id,a.artifact_id).state=='available'
            with pytest.raises(CloudError,match='artifact_not_found'):r.artifacts.get(b.task_id,a.artifact_id)
            monkeypatch.setattr(module,'remove_task_data',original)
            r.results.retention.collect(b.task_id)
            assert r.results.get(b.task_id,rid).phase=='deleted'
            assert rid not in r.registry.get_task(b.task_id).result_ids


def test_download_holds_task_io_until_file_closes(tmp_path):
    async def check(r,b,state):
        lease=await r.artifacts.open_download(b.task_id,state.bundle.files[0].artifact.artifact_id)
        try:
            assert r.task_io.active.get(b.task_id)==1
            newer(r,b);r.results.retention.collect(b.task_id)
            assert r.results.get(b.task_id,state.bundle.plan.result_id).phase=='available'
        finally:await lease.aclose()
        assert b.task_id not in r.task_io.active
        r.results.retention.collect(b.task_id)
        assert r.results.get(b.task_id,state.bundle.plan.result_id).phase=='deleted'
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        with client(r) as c,connect(c,r,b) as ws:
            rid,state=available(r,b,c,ws,tmp_path);asyncio.run(check(r,b,state))


def test_retained_capacity_can_roll_over_more_than_64_rounds(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        r.settings=r.settings.model_copy(update={'result_limits':r.settings.result_limits.model_copy(update={'max_results':1})})
        ids=[]
        for _ in range(70):
            ids.append(newer(r,b));r.results.retention.collect(b.task_id)
            assert r.registry.get_task(b.task_id).result_ids==[ids[-1]]
        assert len(r.results.list(b.task_id,limit=100))==70
        assert r.results.get(b.task_id,ids[0]).phase=='deleted'
        assert r.results.get(b.task_id,ids[-1]).phase=='available'
        lease=r.results.retention.lease(b.task_id,ids[-1],{'lease_id':str(uuid4())})
        with pytest.raises(CloudError,match='result_capacity'):newer(r,b)
        r.results.retention.release(b.task_id,ids[-1],{'lease_id':lease['lease_id']})
        newer(r,b)


def test_latest_follows_admission_order_even_if_clock_moves_backwards(tmp_path):
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r);old=newer(r,b);last=newer(r,b)
        receipt=r.registry.get_command(old)
        r.results.repository.save_receipt(receipt.model_copy(update={'created_at':receipt.created_at+1000}))
        r.results.retention.collect(b.task_id)
        assert r.results.get(b.task_id,last).phase=='available'
        assert r.results.get(b.task_id,old).phase=='deleted'


def test_close_failure_keeps_download_registered_for_retry(tmp_path):
    from types import SimpleNamespace
    from naiyue_test_agent.application.cloud.artifact_download import ArtifactDownload
    class Stream:
        closed=False
        calls=0
        def close(self):
            self.calls+=1
            if self.calls==1:raise OSError('controlled close')
            self.closed=True
    with open_cloud_runtime(config(tmp_path)) as r:
        b=prepared(r)
        async def run():
            lease=ArtifactDownload(r.artifacts,b.task_id);lease.stream=Stream()
            r.task_io._enter(b.task_id);lease.reserved=True;r.artifacts.downloads.add(lease)
            with pytest.raises(OSError):await lease.aclose()
            try:
                assert lease in r.artifacts.downloads and not lease.finished.is_set()
                assert r.task_io.active[b.task_id]==1
            finally:await lease.aclose()
            assert lease.stream.closed and lease.finished.is_set() and lease not in r.artifacts.downloads
            assert b.task_id not in r.task_io.active
        asyncio.run(run())


def test_task_deletion_removes_only_its_result_leases(tmp_path):
    from naiyue_test_agent.application.cloud.backend_runtime import open_cloud_backend
    from lifecycle_helpers import Backend,register
    from test_task_delete import request
    with open_cloud_runtime(config(tmp_path)) as r:
        one=register(r);two=register(r)
        with r.database.transaction(write=True) as db:
            for task in (one,two):
                db.execute('INSERT INTO meta(key,value) VALUES(?,?)',('result-lease:'+task.task_id+':'+str(uuid4())+':'+str(uuid4()),'{}'))
        async def run():
            async with open_cloud_backend(r,backend=Backend()):
                assert (await r.deletions.command(one.task_id,request(r,one.task_id))).status=='succeeded'
        asyncio.run(run())
        with r.database.transaction() as db:
            keys=[v[0] for v in db.execute('SELECT key FROM meta WHERE key LIKE ?',('result-lease:%',))]
        assert len(keys)==1 and keys[0].startswith('result-lease:'+two.task_id+':')
