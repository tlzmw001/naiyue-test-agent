import asyncio
from contextlib import ExitStack
from uuid import uuid4
import httpx
from git_input_helpers import repository,git
from sandbox_session_helpers import SandboxTransport
from test_sandbox_sessions import application
from test_git_delivery_service import prepare
from test_service_ownership import options
from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery


def test_delivery_http_auth_scope_validation_and_explicit_receipt(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            SandboxTransport(tmp_path,monkeypatch,stack)
            config=options(tmp_path);config.frontend_dist.mkdir();(config.frontend_dist/'index.html').write_text('<html/>')
            server=create_service_app(config,application_factory=lambda:application(tmp_path))
            async with server.router.lifespan_context(server):
                record=read_discovery(config);app=server.state.application
                project,host,sid,result=await prepare(app,root,tmp_path)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url=record.origin,
                    headers={'Authorization':'Bearer '+record.access_token,'X-Service-Instance':record.instance_id,'Origin':record.origin}) as client:
                    prefix=f'/api/v1/scopes/{project.project_id}/sessions/{sid}/results/'+ 'c'*32+'/delivery'
                    assert (await client.get(prefix,headers={'Authorization':''})).status_code==401
                    assert (await client.get(f'/api/v1/scopes/chats/sessions/{sid}/results/'+ 'c'*32+'/delivery')).status_code==404
                    view=await client.get(prefix);assert view.status_code==200,view.text
                    assert view.json()['actions']['branch']['available']
                    assert set(view.json()['actions'])=={'branch','merge','discard'}
                    assert 'remotes' not in view.json()
                    for unsupported in (
                        dict(operation_id='1'*32,action='push',branch='task',remote_name='origin',remote_url='https://example.invalid/repo'),
                        dict(operation_id='2'*32,action='pr'),
                        dict(operation_id='3'*32,action='branch',branch='task',remote_name='origin'),
                    ):
                        denied=await client.post(prefix,json=unsupported,headers={'X-Request-ID':str(uuid4())})
                        assert denied.status_code==422,denied.text
                    assert (await client.get(prefix+'/operations')).json()['items']==[]
                    command=dict(operation_id='d'*32,action='branch',branch='review')
                    invalid=await client.post(prefix,json={**command,'workspace_root':str(root)},headers={'X-Request-ID':str(uuid4())})
                    assert invalid.status_code==422,invalid.text
                    made=await client.post(prefix,json=command,headers={'X-Request-ID':str(uuid4())})
                    assert made.status_code==200,made.text
                    assert made.json()['state']=='completed'
                    assert git(root,'rev-parse','refs/heads/review').decode().strip()==result['receipt']['output_commit']
                    assert len((await client.get(prefix+'/operations')).json()['items'])==1
                    repeated=await client.post(prefix,json=command,headers={'X-Request-ID':str(uuid4())})
                    assert repeated.json()==made.json()
    asyncio.run(scenario())
