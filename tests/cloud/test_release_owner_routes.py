import asyncio
from uuid import uuid4
import httpx
from lifecycle_helpers import config,template
from cloud_helpers import owner_headers
from test_release_updates import release
from naiyue_test_agent.application.cloud.runtime import open_cloud_runtime
from naiyue_test_agent.service.cloud.app import create_cloud_apps


def test_release_owner_auth_strict_body_and_task_listener_separation(tmp_path):
    async def scenario():
        with open_cloud_runtime(config(tmp_path)) as r:
            owner,task=create_cloud_apps(r);body=dict(command_id=str(uuid4()),release=release().model_dump(),template=template(runner_release_id='r2').model_dump())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=owner),base_url=r.settings.origin) as client:
                assert (await client.post('/api/cloud/v1/releases/register',json=body)).status_code==401
                headers=owner_headers(r)
                ok=await client.post('/api/cloud/v1/releases/register',json=body,headers=headers)
                assert ok.status_code==200 and ok.json()['phase']=='ready'
                bad=await client.post('/api/cloud/v1/releases/register',json=body|dict(shell='ignored?'),headers=headers)
                assert bad.status_code==422
                active=await client.get('/api/cloud/v1/releases/active/python-tests',headers=headers)
                assert active.json()==dict(release_id=None,revision=0,inventory_revision=0)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=task),base_url='http://'+r.settings.task_listen) as client:
                assert (await client.post('/api/cloud/v1/releases/register',json=body,headers=owner_headers(r))).status_code==404
    asyncio.run(scenario())
