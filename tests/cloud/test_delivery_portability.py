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
    verified=SimpleNamespace(ordinary=False,output_manifest=SimpleNamespace(entries=[entry]),changes=[dict(path=path,old=None,new=entry)])
    assert preflight(tmp_path,verified)==[path]
    assert branch_preflight(verified,tmp_path/'unused-receipt',tmp_path,'new-code',None)==[path]
    with pytest.raises(CloudError,match='cloud_delivery_path_unsupported'):
        export_tree(tmp_path,'export',verified,tmp_path/'journal')
    assert list(tmp_path.iterdir())==[]


def test_result_view_does_not_require_local_git_source(tmp_path,monkeypatch):
    async def run():
        manager,domain,store,sid,remote,binding,rid,*_=await receiving(tmp_path)
        try:
            original=domain.repo.operation
            def scrubbed(cid,session):
                value=original(cid,session)
                return value|{'request':{'command_id':cid},'error_code':'cloud_task_deleted'}
            monkeypatch.setattr(domain.repo,'operation',scrubbed)
            result=await manager.view(domain,binding['binding_id'],sid,'result',{'result_id':rid})
            assert result['plan']['result_id']==rid
        finally:await manager.close();remote.http.close();store.close()
    asyncio.run(run())
