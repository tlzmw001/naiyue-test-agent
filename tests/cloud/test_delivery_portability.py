"""All delivery modes report nonportable output paths before user writes."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path
import pytest
from naiyue_test_agent.contracts.cloud_results import OutputEntry
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.cloud_files.delivery_apply import preflight,export_tree
from naiyue_test_agent.cloud_files.delivery_branch import branch_preflight
from test_result_receive import receiving


@pytest.mark.parametrize('path',['report:notes','CON','sub/LPT¹.txt'])
def test_all_delivery_modes_reject_nonportable_output_before_materializing(tmp_path,path):
    entry=OutputEntry(path=path,type='file',size=0,sha256='a'*64,mode=0o644)
    verified=SimpleNamespace(output_manifest=SimpleNamespace(entries=[entry]),changes=[dict(path=path,old=None,new=entry)])
    assert preflight(tmp_path,verified)==[path]
    assert branch_preflight(verified,tmp_path/'unused-receipt',tmp_path,'new-code',None)==[path]
    with pytest.raises(CloudError,match='cloud_delivery_path_unsupported'):
        export_tree(tmp_path,'export',verified,tmp_path/'journal')
    assert list(tmp_path.iterdir())==[]


def test_result_view_returns_unavailable_after_source_request_is_scrubbed(tmp_path,monkeypatch):
    async def run():
        manager,domain,store,sid,remote,binding,rid,*_=await receiving(tmp_path)
        try:
            original=domain.repo.operation
            def scrubbed(cid,session):
                value=original(cid,session)
                return value|{'request':{'command_id':cid},'error_code':'cloud_task_deleted'}
            monkeypatch.setattr(domain.repo,'operation',scrubbed)
            with pytest.raises(CloudError,match='cloud_task_unavailable'):
                await manager.view(domain,binding['binding_id'],sid,'result',{'result_id':rid})
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())


@pytest.mark.parametrize('action',['apply','branch','export'])
def test_formal_preflight_lists_unsupported_paths_without_force_or_unpack(tmp_path,monkeypatch,action):
    from naiyue_test_agent.application.cloud_local.deliveries import LocalDeliveries
    from naiyue_test_agent.contracts.cloud_delivery import DeliveryRecord
    from naiyue_test_agent.contracts.cloud_results import OutputManifest
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    entry=OutputEntry(path='CON',type='file',size=0,sha256='a'*64,mode=0o644)
    manifest=OutputManifest(input_id='a'*64,policy_sha256='b'*64,entries=[entry.model_dump()])
    saved=[];record=DeliveryRecord(command_id=str(uuid4()),local_project_id=str(uuid4()),local_session_id=uuid4().hex,
        receive_id=str(uuid4()),action=action,name='incoming' if action!='apply' else '',phase='preparing')
    meta={};domain=SimpleNamespace(deliveries=SimpleNamespace(read=lambda *args:(record,meta),save=lambda *args:saved.append(args)),
        materials=SimpleNamespace(_get=lambda *args:None,files=SimpleNamespace(read=lambda *args:manifest.model_dump_json())))
    service=LocalDeliveries(SimpleNamespace(settings=CloudTaskSettings()));service._check_root=lambda *args:tmp_path
    receive=SimpleNamespace(files=[SimpleNamespace(role=role,material_id=str(uuid4()),artifact=None) for role in ('manifest','patch')])
    def forbidden(*args):raise AssertionError('unsafe output must be rejected before unpack')
    monkeypatch.setattr('naiyue_test_agent.cloud_files.delivery_verify.verify_delivery',forbidden)
    service._run(domain,record,receive,None,None,tmp_path,False)
    result,metadata=saved[0]
    assert result.conflicts==['CON'] and result.force_allowed is False and metadata['verified'] is False
    assert result.error_code=='cloud_delivery_path_unsupported'
