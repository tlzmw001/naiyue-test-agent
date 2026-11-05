"""Browser links carry no owner credential and require their scoped HttpOnly cookie."""
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
