"""Result endpoints retain service authentication and project/session ownership."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import httpx

from git_input_helpers import repository
from sandbox_session_helpers import SandboxTransport
from test_sandbox_sessions import application
from test_service_ownership import options
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery


def test_http_result_owner_validation_selection_and_offline_diff(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            config=options(tmp_path);config.frontend_dist.mkdir();(config.frontend_dist/'index.html').write_text('<html/>')
            server=create_service_app(config,application_factory=lambda:application(tmp_path))
            async with server.router.lifespan_context(server):
                record=read_discovery(config);app=server.state.application
                project=await app.projects.add_local(root,trusted=True,operation_id='P');host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id'];(tmp_path/binding/'app.txt').write_text('agent\n')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url=record.origin,
                    headers={'Authorization':'Bearer '+record.access_token,'X-Service-Instance':record.instance_id,'Origin':record.origin}) as client:
                    prefix=f'/api/v1/scopes/{project.project_id}/sessions/{sid}/results'
                    assert (await client.get(prefix,headers={'Authorization':''})).status_code==401
                    assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/results')).status_code==404
                    assert (await client.get(prefix+'/options')).status_code==200
                    key={'X-Request-ID':str(uuid4())}
                    invalid=await client.post(prefix,json={'result_id':'c'*32,'include_untracked':['../escape']},headers=key)
                    assert invalid.status_code==422,invalid.text
                    made=await client.post(prefix,json={'result_id':'c'*32,'include_untracked':[]},headers={'X-Request-ID':str(uuid4())})
                    assert made.status_code==200,made.text
                    assert made.json()['state']=='ready'
                    transport.expired.add(binding)
                    listed=await client.get(prefix);assert listed.status_code==200 and len(listed.json()['items'])==1
                    result=await client.get(prefix+'/'+'c'*32);assert result.json()['state']=='ready'
                    diff=await client.get(prefix+'/'+'c'*32+'/diff',params={'path':'app.txt'})
                    assert diff.status_code==200 and '+agent' in diff.json()['patch'],diff.text
                    transport.expired.clear()
    asyncio.run(scenario())
