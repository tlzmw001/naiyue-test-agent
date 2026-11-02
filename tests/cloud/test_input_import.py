import asyncio
import hashlib
from uuid import uuid4

import httpx
import pytest

from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps
from naiyue_test_agent.contracts.cloud_inputs import InputLimits
from cloud_helpers import settings,owner_headers
from lifecycle_helpers import template
from test_input_formats import package


def transfer_request(captured):
    return dict(command_id=str(uuid4()),purpose='input',input_id=captured.input_id,
        size=captured.size,archive_sha256=captured.archive_sha256,codec=captured.codec)


def test_owner_import_commit_create_task_and_cache_hit(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path,templates={'python-tests':template()})) as runtime:
        async def run():
            owner,task=create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin,headers=owner_headers(runtime)) as client:
                project=(await client.post('/api/cloud/v1/projects',json={'command_id':str(uuid4()),'name':'input project'})).json()['project']
                request=transfer_request(captured);response=await client.post('/api/cloud/v1/transfers',json=request)
                assert response.status_code==201,response.text
                receipt=response.json();transfer=receipt['result']['transfer'];tid=transfer['transfer_id']
                assert (await client.post('/api/cloud/v1/transfers',json=request)).json()==receipt
                task_request=dict(command_id=str(uuid4()),project_id=project['project_id'],input_id=captured.input_id,template_id='python-tests')
                assert (await client.post('/api/cloud/v1/tasks',json=task_request)).status_code==409
                raw=(captured.directory/'archive').read_bytes();headers={'Content-Type':'application/octet-stream','Upload-Offset':'0','X-Content-SHA256':captured.archive_sha256}
                uploaded=await client.put(f'/api/cloud/v1/transfers/{tid}/bytes',content=raw,headers=headers)
                assert uploaded.status_code==200,uploaded.text
                assert uploaded.json()['offset']==len(raw)
                assert (await client.put(f'/api/cloud/v1/transfers/{tid}/bytes',content=raw,headers=headers)).json()==uploaded.json()
                commit=await client.post(f'/api/cloud/v1/transfers/{tid}/commit',json={})
                assert commit.status_code==200,commit.text
                assert commit.json()['input_id']==captured.input_id
                assert (await client.post(f'/api/cloud/v1/transfers/{tid}/commit',json={})).json()==commit.json()
                created=await client.post('/api/cloud/v1/tasks',json=task_request)
                assert created.status_code==201,created.text
                assert created.json()['result']['task']['input_id']==captured.input_id
                assert (await client.post('/api/cloud/v1/tasks',json=task_request)).json()==created.json()
                cached=await client.post('/api/cloud/v1/transfers',json=transfer_request(captured))
                assert cached.json()['result']['transfer']['cache_hit'] is True
                assert (await client.delete(f'/api/cloud/v1/transfers/{tid}')).status_code==409
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+runtime.settings.task_listen) as client:
                assert (await client.post('/api/cloud/v1/transfers',json=request)).status_code==404
        asyncio.run(run())


def test_short_or_bad_hash_upload_never_advances_and_can_restart(tmp_path):
    captured,_,_=package(tmp_path)
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        async def run():
            owner,_=create_cloud_apps(runtime)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin,headers=owner_headers(runtime)) as c:
                t=(await c.post('/api/cloud/v1/transfers',json=transfer_request(captured))).json()['result']['transfer'];tid=t['transfer_id']
                headers={'Content-Type':'application/octet-stream','Upload-Offset':'0','X-Content-SHA256':captured.archive_sha256,'Content-Length':str(captured.size)}
                short=await c.put(f'/api/cloud/v1/transfers/{tid}/bytes',content=b'x',headers=headers)
                assert short.status_code==409
                assert (await c.get(f'/api/cloud/v1/transfers/{tid}')).json()['offset']==0
                bad=await c.put(f'/api/cloud/v1/transfers/{tid}/bytes',content=b'x'*captured.size,headers=headers)
                assert bad.status_code==409
                assert (await c.post(f'/api/cloud/v1/transfers/{tid}/commit',json={})).status_code==409
                assert (await c.get('/api/cloud/v1/inputs/'+captured.input_id)).status_code==404
                good=await c.put(f'/api/cloud/v1/transfers/{tid}/bytes',content=(captured.directory/'archive').read_bytes(),headers=headers)
                assert good.status_code==200
                assert (await c.delete(f'/api/cloud/v1/transfers/{tid}')).status_code==200
                assert (await c.get(f'/api/cloud/v1/transfers/{tid}')).json()['state']=='cancelled'
        asyncio.run(run())


def test_unauthorized_body_is_not_consumed(tmp_path):
    with open_cloud_runtime(settings(tmp_path)) as runtime:
        async def run():
            owner,_=create_cloud_apps(runtime);consumed=[]
            async def body():consumed.append(True);yield b'not read'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=runtime.settings.origin) as c:
                response=await c.put('/api/cloud/v1/transfers/'+str(uuid4())+'/bytes',content=body())
                assert response.status_code==401 and consumed==[]
        asyncio.run(run())
