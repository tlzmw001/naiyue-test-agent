"""The authenticated service shares creation, trust and setup facts with Web/CLI."""
import asyncio
from contextlib import ExitStack
from uuid import uuid4
import httpx

from naiyue_test_agent.service.app import create_service_app
from naiyue_test_agent.service.ownership import read_discovery
from test_service_ownership import options
from test_sandbox_sessions import application
from sandbox_session_helpers import SandboxTransport
from git_input_helpers import repository


def test_http_sandbox_creation_is_authenticated_idempotent_and_reports_binding(tmp_path,monkeypatch):
    root=repository(tmp_path);(root/'pick.txt').write_text('explicit')
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            config=options(tmp_path);config.frontend_dist.mkdir();(config.frontend_dist/'index.html').write_text('<html/>')
            server=create_service_app(config,application_factory=lambda:application(tmp_path))
            async with server.router.lifespan_context(server):
                record=read_discovery(config);app=server.state.application
                project=await app.projects.add_local(root,trusted=True,operation_id='P')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url=record.origin,
                    headers={'Authorization':'Bearer '+record.access_token,'X-Service-Instance':record.instance_id,'Origin':record.origin}) as client:
                    prefix=f'/api/v1/scopes/{project.project_id}'
                    choice=await client.get(f'/api/v1/projects/{project.project_id}/execution-options')
                    assert choice.status_code==200,choice.text
                    assert choice.json()['untracked']==['pick.txt']
                    key=str(uuid4());body=dict(model_id='fake',execution='sandbox',include_untracked=['pick.txt'])
                    denied=await client.post(prefix+'/sessions',json=body,headers={'Authorization':'','X-Request-ID':key})
                    assert denied.status_code==401 and transport.created==[]
                    first=await client.post(prefix+'/sessions',json=body,headers={'X-Request-ID':key})
                    assert first.status_code==201,first.text
                    second=await client.post(prefix+'/sessions',json=body,headers={'X-Request-ID':key})
                    assert first.json()==second.json() and len(transport.created)==1
                    sid=first.json()['session_id']
                    for endpoint in (prefix+'/sessions',f'/api/v1/navigation/{project.project_id}/sessions'):
                        listed=(await client.get(endpoint)).json()
                        assert listed[0]['execution_binding']=='sandbox'
                        assert {'thread_id','execution_setup_json','active_tool_names_json'}.isdisjoint(listed[0])
                    view=await client.post(prefix+f'/sessions/{sid}/prepare',headers={'X-Request-ID':str(uuid4())})
                    assert view.status_code==200,view.text
                    assert view.json()['execution']['kind']=='sandbox' and view.json()['can_run']
                    assert (tmp_path/view.json()['execution']['binding_id']/'pick.txt').read_text()=='explicit'
                    ready=await client.post(prefix+f'/sessions/{sid}/environment/prepare',headers={'X-Request-ID':str(uuid4())})
                    assert ready.status_code==200 and ready.json()['state']=='ready',ready.text
                    tasks=await client.get(prefix+'/background-tasks',params={'session_id':sid})
                    assert tasks.status_code==200 and tasks.json()['items']==[],tasks.text
                    all_tasks=await client.get(prefix+'/background-tasks')
                    assert all_tasks.status_code==200 and all_tasks.json()['items']==[],all_tasks.text
                    await server.state.scopes.close_project(project.project_id)
                    assert project.project_id not in server.state.scopes.services
                    cold=await client.get(f'/api/v1/navigation/{project.project_id}/sessions')
                    assert cold.status_code==200 and cold.json()[0]['execution_binding']=='sandbox'
                    assert project.project_id not in server.state.scopes.services
                    assert len(transport.created)==1
    asyncio.run(scenario())


def test_git_http_history_trust_and_scope_binding_have_no_host_fallback(tmp_path,monkeypatch):
    root=repository(tmp_path)
    async def scenario():
        with ExitStack() as stack:
            transport=SandboxTransport(tmp_path,monkeypatch,stack)
            config=options(tmp_path);config.frontend_dist.mkdir();(config.frontend_dist/'index.html').write_text('<html/>')
            server=create_service_app(config,application_factory=lambda:application(tmp_path))
            async with server.router.lifespan_context(server):
                record=read_discovery(config);app=server.state.application
                project=await app.projects.add_git(root.as_uri(),ref='HEAD',name='remote',operation_id='G')
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server),base_url=record.origin,
                    headers={'Authorization':'Bearer '+record.access_token,'X-Service-Instance':record.instance_id,'Origin':record.origin}) as client:
                    prefix=f'/api/v1/scopes/{project.project_id}'
                    assert (await client.get(prefix+'/sessions')).json()==[]
                    command=lambda:{'X-Request-ID':str(uuid4())}
                    denied=await client.post(prefix+'/sessions',json=dict(model_id='fake',execution='sandbox'),headers=command())
                    assert denied.status_code==409 and not (await app.projects.get(project.project_id)).trusted
                    stale=await client.post(f'/api/v1/projects/{project.project_id}/trust',json=dict(trusted=True,expected_revision=project.revision+1),headers=command())
                    assert stale.status_code==409
                    trusted=await client.post(f'/api/v1/projects/{project.project_id}/trust',json=dict(trusted=True,expected_revision=project.revision),headers=command())
                    assert trusted.status_code==200,trusted.text
                    denied=await client.post(prefix+'/sessions',json=dict(model_id='fake'),headers=command())
                    assert denied.status_code==409 and transport.created==[]
                    made=await client.post(prefix+'/sessions',json=dict(model_id='fake',execution='sandbox'),headers=command())
                    assert made.status_code==201,made.text
                    sid=made.json()['session_id']
                    assert [row['session_id'] for row in (await client.get(prefix+'/sessions')).json()]==[sid]
                    assert (await client.get('/api/v1/scopes/chats/sessions/'+sid)).status_code==404
    asyncio.run(scenario())
