"""Browser links carry no owner credential and require their scoped HttpOnly cookie."""
import asyncio
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI,APIRouter
from fastapi.testclient import TestClient
from starlette.responses import Response,JSONResponse
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def test_browser_download_cookie_is_scoped_expiring_and_bound_to_fixed_file(monkeypatch):
    from naiyue_test_agent.service.cloud_downloads import register
    from naiyue_test_agent.cloud_task_settings import CloudTaskSettings
    from naiyue_test_agent.application.cloud_local import result_download
    app=FastAPI();router=APIRouter(prefix='/api/client/v1');calls=[];now=[1.0]
    app.state.web_settings=SimpleNamespace(origin='http://testserver',authority='testserver')
    app.state.owner=SimpleNamespace(record=SimpleNamespace(instance_id='instance'))
    manager=SimpleNamespace(settings=CloudTaskSettings(),closing=False)
    app.state.scopes=SimpleNamespace(cloud_tasks=manager,closing=False)
    async def domain(request,pid,sid):return None,'domain'
    async def view(*args):return {'phase':'available','bundle':{'files':[{'artifact':{'artifact_id':fid}}]}}
    manager.view=view
    def access(request):
        if request.headers.get('authorization')!='Bearer local':raise CloudError('unauthorized',401)
        return manager
    async def respond(request,mgr,local,bid,sid,rid,file_id):
        calls.append((bid,sid,rid,file_id));return Response(b'file')
    monkeypatch.setattr(result_download,'response',respond)
    monkeypatch.setattr('naiyue_test_agent.service.cloud_downloads.time.monotonic',lambda:now[0])
    register(router,access,domain);app.include_router(router)
    @app.exception_handler(CloudError)
    async def error(request,exc):return JSONResponse({'error':exc.code},status_code=exc.status)
    bid,rid,fid,pid=map(str,(uuid4(),uuid4(),uuid4(),uuid4()))
    path=f'/api/client/v1/cloud-tasks/{bid}/results/{rid}/files/{fid}/download-link?local_project_id={pid}&local_session_id=session'
    with TestClient(app) as c:
        assert c.post(path).status_code==401
        issued=c.post(path,headers={'Authorization':'Bearer local','Origin':'http://testserver'})
        assert issued.status_code==200
        url=issued.json()['url'];assert 'local' not in url and 'token' not in url
        cookie=issued.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Path='+url in cookie
        assert c.get(url,headers={'Origin':'http://foreign'}).status_code==403
        assert c.get(url).content==b'file' and calls==[(bid,'session',rid,fid)]
        saved=dict(c.cookies);c.cookies.clear();assert c.get(url).status_code==403
        c.cookies.update(saved);now[0]+=manager.settings.download_link_seconds+1
        assert c.get(url).status_code==403


def test_cookie_download_uses_real_service_without_owner_bearer(monkeypatch,tmp_path):
    from naiyue_test_agent.application.cloud_local import result_download
    from naiyue_test_agent.service.ownership import read_discovery
    from test_async_application import StreamingToolModel
    from test_service_protocol import service_clients

    async def scenario():
        async with service_clients(monkeypatch,tmp_path,StreamingToolModel(replies=[])) as (client,other,app,options):
            project_path=tmp_path/'download-project'
            project_path.mkdir()
            project=await app.state.application.projects.add_local(project_path,trusted=True,operation_id='download-project')
            host=await app.state.application.projects.open(project.project_id)
            sid=await host.sessions.create('A')
            bid,rid,fid=map(str,(uuid4(),uuid4(),uuid4()))
            downloads=[]

            async def view(local,binding_id,session_id,kind,params):
                assert (local,binding_id,session_id,kind,params)==(host.cloud_tasks,bid,sid,'result',{'result_id':rid})
                return {'phase':'available','bundle':{'files':[{'artifact':{'artifact_id':fid}}]}}

            async def respond(request,manager,local,binding_id,session_id,result_id,file_id):
                downloads.append((local,binding_id,session_id,result_id,file_id))
                return Response(b'file')

            monkeypatch.setattr(app.state.scopes.cloud_tasks,'view',view)
            monkeypatch.setattr(result_download,'response',respond)
            issue=f'/api/client/v1/cloud-tasks/{bid}/results/{rid}/files/{fid}/download-link'
            params={'local_project_id':project.project_id,'local_session_id':sid}
            instance={'X-Service-Instance':read_discovery(options).instance_id}
            assert (await other.post(issue,params=params,headers={**instance,'Authorization':''})).status_code==401
            issued=await client.post(issue,params=params,headers=instance)
            assert issued.status_code==200
            url=issued.json()['url']
            downloaded=await client.get(url,headers={'Authorization':''})
            assert downloaded.status_code==200, downloaded.text
            assert downloaded.content==b'file'
            assert downloads==[(host.cloud_tasks,bid,sid,rid,fid)]
            rejected=await other.get(url,headers={'Authorization':''})
            assert rejected.status_code==403 and rejected.json()['error']['code']=='download_link_invalid'
            foreign=await client.get(url,headers={'Authorization':'','Origin':'https://foreign.example'})
            assert foreign.status_code==403 and foreign.json()['error']['code']=='download_link_invalid'
            assert (await client.get('/api/client/v1/cloud-profiles',headers={'Authorization':''})).status_code==401
            assert downloads==[(host.cloud_tasks,bid,sid,rid,fid)]
    asyncio.run(scenario())
