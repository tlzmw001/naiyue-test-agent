"""Short-lived browser download cookies authorize one immutable file, never an endpoint."""
import secrets,time
from uuid import uuid4
from fastapi import Request
from starlette.responses import JSONResponse
from naiyue_test_agent.contracts.cloud_tasks import UUIDText,CloudError
from naiyue_test_agent.contracts.cloud_local_tasks import SessionId

BROWSER_DOWNLOAD_ROUTE = 'cloud_browser_download'


def register(router,access,domain):
    @router.post('/cloud-tasks/{binding_id}/results/{result_id}/files/{file_id}/download-link')
    async def issue(request:Request,binding_id:UUIDText,result_id:UUIDText,file_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
        manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
        record=await manager.view(local,binding_id,local_session_id,'result',dict(result_id=result_id))
        if record['phase']!='available' or not any(f['artifact']['artifact_id']==file_id for f in (record.get('bundle') or {}).get('files',[])):
            raise CloudError('result_file_not_found',404)
        state=request.app.state;now=time.monotonic()
        tickets={key:value for key,value in getattr(state,'cloud_download_tickets',{}).items() if value['expires']>now}
        if len(tickets)>=manager.settings.max_download_links:raise CloudError('cloud_download_capacity',429)
        ident=str(uuid4());secret=secrets.token_urlsafe(32);ttl=manager.settings.download_link_seconds
        path='/api/client/v1/cloud-downloads/'+ident
        tickets[ident]=dict(secret=secret,expires=now+ttl,instance=state.owner.record.instance_id,
            project=local_project_id,session=local_session_id,binding=binding_id,result=result_id,file=file_id)
        state.cloud_download_tickets=tickets
        response=JSONResponse(dict(url=path,expires_in=ttl),headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        response.set_cookie('nyt_download',secret,max_age=ttl,path=path,httponly=True,samesite='strict')
        return response

    @router.get('/cloud-downloads/{link_id}',name=BROWSER_DOWNLOAD_ROUTE)
    async def download(request:Request,link_id:UUIDText):
        state=request.app.state;settings=state.web_settings
        if request.headers.get('host')!=settings.authority or request.headers.get('origin') not in (None,settings.origin):
            raise CloudError('download_link_invalid',403)
        if request.headers.get('sec-fetch-site') not in (None,'same-origin','none'):raise CloudError('download_link_invalid',403)
        ticket=getattr(state,'cloud_download_tickets',{}).get(link_id)
        provided=request.cookies.get('nyt_download','')
        if (not ticket or ticket['expires']<=time.monotonic() or ticket['instance']!=state.owner.record.instance_id
                or not secrets.compare_digest(provided.encode(),ticket['secret'].encode())):raise CloudError('download_link_invalid',403)
        if state.scopes.closing:raise CloudError('cloud_client_closing',503)
        manager=state.scopes.cloud_tasks
        _,local=await domain(request,ticket['project'],ticket['session'])
        from naiyue_test_agent.application.cloud_local.result_download import response
        return await response(request,manager,local,ticket['binding'],ticket['session'],ticket['result'],ticket['file'])
