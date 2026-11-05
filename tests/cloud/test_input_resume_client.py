"""The real owner API receives only unconfirmed segments after each lost ACK."""
import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.cloud_files.client import CloudClient,CloudConnection
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from cloud_helpers import settings
from test_input_formats import package


@pytest.mark.parametrize('phase',['create','bytes','commit'])
def test_client_resume_uses_real_server_offset_after_ack_loss(tmp_path,phase):
    captured,_,_=package(tmp_path);writes=[];ids=[];lost=False
    cfg=settings(tmp_path,input_limits=InputLimits(chunk_bytes=64))
    with open_cloud_runtime(cfg) as r,TestClient(create_cloud_apps(r)[0],base_url=cfg.origin) as owner:
        connection=CloudConnection(endpoint=cfg.origin,owner_authority=cfg.authority,
            owner_token=cfg.access_token,instance_id=r.instance_id)
        def handler(request):
            nonlocal lost
            if request.method=='POST' and request.url.path.endswith('/transfers'):ids.append(json.loads(request.content)['command_id'])
            if request.method=='PUT':writes.append(int(request.headers['Upload-Offset']))
            response=owner.request(request.method,str(request.url),headers=dict(request.headers),content=request.content)
            matches=(phase=='create' and request.url.path.endswith('/transfers') or phase=='bytes' and request.method=='PUT'
                and request.headers['Upload-Offset']=='64' or phase=='commit' and request.url.path.endswith('/commit'))
            if matches and response.status_code<300 and not lost:
                lost=True;raise httpx.ReadError('controlled lost segment ACK',request=request)
            return httpx.Response(response.status_code,content=response.content,headers=response.headers)
        progress=[]
        for attempt in range(2):
            progress.append([])
            user=CloudClient(connection,transport=httpx.MockTransport(handler))
            try:
                if attempt==0:
                    with pytest.raises(CloudError):user.upload(captured.directory,on_progress=lambda offset,total: progress[-1].append((offset,total)))
                else:result=user.upload(captured.directory,on_progress=lambda offset,total: progress[-1].append((offset,total)))
            finally:user.close()
        assert progress[1][-1]==(captured.size,captured.size)
        assert all(total==captured.size and 0<=offset<=total for updates in progress for offset,total in updates)
        if phase=='bytes':
            assert [offset for offset,_ in progress[0]]==[0,64]
            assert progress[1][0]==(128,captured.size)
        assert lost and len(set(ids))==1 and len(writes)==len(set(writes))
        assert writes==list(range(0,captured.size,64))
        assert result['input_id']==captured.input_id and r.inputs.get_input(captured.input_id).input_id==captured.input_id
