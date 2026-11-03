"""Delivery intent owns its project/session and survives an interrupted writer."""
from uuid import uuid4
import pytest
from naiyue_test_agent.contracts.cloud_delivery import DeliveryRequest,DeliveryRecord
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from test_manual_task_bindings import populated


def test_delivery_intent_identity_progress_and_session_purge(tmp_path):
    store,project,sid=populated(tmp_path)
    with store:
        cid=str(uuid4())
        request=DeliveryRequest(command_id=cid,local_session_id=sid,receive_id=str(uuid4()),action='apply',destination_handle=project)
        record=DeliveryRecord(command_id=cid,local_session_id=sid,local_project_id=project,
            receive_id=request.receive_id,action='apply',phase='prepared')
        repo=store.cloud_deliveries
        assert repo.reserve(request,record,dict(root_identity={'device':1,'inode':2}),10)==record
        assert repo.reserve(request,record,{},10)==record
        with pytest.raises(CloudError):repo.reserve(request.model_copy(update={'name':'other'}),record,{},10)
        with pytest.raises(CloudError):repo.read(cid,'other')
        repo.save(record.model_copy(update={'phase':'executing'}))
        assert repo.read(cid,sid)[0].phase=='executing'
        assert repo.read(cid,sid)[1]['root_identity']=={'device':1,'inode':2}
        from naiyue_test_agent.storage.errors import SessionPersistenceError
        with pytest.raises(SessionPersistenceError,match='session_cloud_delivery_active'):
            store.runs.delete(sid,())
        repo.save(record.model_copy(update={'phase':'completed'}))
        store.runs.delete(sid,())
        assert not store.database.connection.execute('SELECT 1 FROM naiyue_cloud_deliveries').fetchall()


def test_active_delivery_deletion_failure_keeps_its_public_reason():
    from naiyue_test_agent.web.errors import public_error
    from naiyue_test_agent.storage.errors import SessionPersistenceError
    assert public_error(SessionPersistenceError('session_cloud_delivery_active')).code=='session_cloud_delivery_active'
