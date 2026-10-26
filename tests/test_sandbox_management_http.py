"""Global lifecycle management keeps authentication, exact IDs and confirmation."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4

import httpx

from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery
from sandbox_session_helpers import SandboxTransport
from test_sandbox_profile_sessions import application
from test_service_ownership import options
from git_input_helpers import repository


def test_management_auth_confirmation_replay_and_busy_retry(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            config=options(tmp_path)
            server=create_service_app(config,application_factory=lambda:application(tmp_path,{'image':'fixture:test'}))
            async with server.router.lifespan_context(server):
                record=read_discovery(config);app=server.state.application
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                host=await app.projects.open(project.project_id)
                sid=await host.sessions.create_sandbox('fake',request_id=str(uuid4()))
                binding=(await host.sessions.status(sid))['execution']['binding_id']
                prefix='/api/v1/environments'
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url=record.origin,
                    headers={'Authorization':'Bearer '+record.access_token,'X-Service-Instance':record.instance_id,
                        'Origin':record.origin}) as client:
                    assert (await client.get(prefix,headers={'Authorization':''})).status_code==401
                    view=(await client.get(prefix)).json()
                    assert view['usage']==dict(running=1,retained=1)
                    row=view['items'][0]
                    assert row['binding_id']==binding and row['project_name']==project.name
                    assert not row['busy'] and not row['unsettled']
                    assert not {'settings','context','image','container_id'} & row.keys()
                    async def action(action,body=None,**headers):
                        return await client.post(f'{prefix}/{binding}/{action}',json=body or {},
                            headers={'X-Request-ID':str(uuid4()),**headers})
                    assert (await action('stop',Authorization='')).status_code==401
                    assert (await action('stop',**{'X-Service-Instance':'0'*32})).status_code==409
                    assert (await action('stop',Origin='http://foreign.invalid')).status_code==403
                    assert (await action('destroy',{'confirm_destroy':'yes'})).status_code==422
                    denied=await action('destroy')
                    assert denied.status_code==409 and denied.json()['error']['code']=='env_confirmation_required'
                    key={'X-Request-ID':str(uuid4())}
                    async with app.environments.using(binding):
                        busy=await action('stop',**key)
                        assert busy.json()['error']['code']=='env_busy'
                        assert (await client.get(prefix)).json()['items'][0]['busy']
                    stopped=await action('stop',**key)
                    assert stopped.status_code==200 and stopped.json()['lifecycle']=='stopped'
                    before=len(transport.actions)
                    assert (await action('stop',**key)).json()==stopped.json()
                    assert len(transport.actions)==before
                    assert (await client.get(prefix)).json()['usage']==dict(running=0,retained=1)
                    assert (await action('start')).json()['lifecycle']=='running'
                    assert (await action('destroy',{'confirm_destroy':True})).json()['lifecycle']=='destroyed'
                    assert (await client.get(prefix)).json()['items']==[]
                    assert (await host.history.session(sid))['session_id']==sid
    asyncio.run(scenario())
