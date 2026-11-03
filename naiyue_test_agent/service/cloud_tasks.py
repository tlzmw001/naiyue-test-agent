"""Local authenticated, bounded manual delegation; no arbitrary remote task IDs."""
from fastapi import APIRouter,Request,Query
from pydantic import ValidationError
from starlette.responses import JSONResponse
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText,CloudRecord
from naiyue_test_agent.contracts.cloud_json import parse_object
from naiyue_test_agent.contracts.cloud_local_tasks import ManualCreate,LocalSourceRequest,SessionId
from naiyue_test_agent.contracts.cloud_local_tasks import ManualDelete
from naiyue_test_agent.contracts.cloud_local_tasks import ManualMessage,ManualControl,ManualEnvironment,TaskDraft,command_summary
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.web.auth import authorize
from naiyue_test_agent.contracts.cloud_history import Identity
from typing import Literal

router=APIRouter(prefix='/api/client/v1')


def command_response(request,value,*,status_code=200):
    if isinstance(value,dict) and 'operation' in value:
        value=value|{'operation':command_summary(value['operation'])}
    elif isinstance(value,dict) and 'request' in value and 'steps' in value:value=command_summary(value)
    from naiyue_test_agent.web.serialization import encoded_size
    if encoded_size(value)>request.app.state.web_settings.max_frame_bytes:raise CloudError('cloud_response_too_large',502)
    return JSONResponse(value,status_code=status_code)


def access(request):
    authorize(request,request.app.state.web_settings)
    if request.headers.get('x-service-instance')!=request.app.state.owner.record.instance_id:
        raise CloudError('service_instance_mismatch',409)
    scopes=request.app.state.scopes
    if scopes.closing:raise CloudError('cloud_client_closing',503)
    return scopes.cloud_tasks


async def body(request,model):
    # New prefix deliberately does not use the old in-memory command middleware.
    limit=request.app.state.web_settings.max_frame_bytes;raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>limit:raise CloudError('request_too_large',413)
    value=parse_object(bytes(raw),byte_limit=limit,depth_limit=32)
    try:return model.model_validate(value)
    except ValidationError:raise CloudError('invalid_request',422) from None


async def domain(request,project_id,session_id):
    app=request.app.state.application
    project=await app.projects.get(project_id)
    if project.lifecycle!='active':raise CloudError('cloud_project_unavailable')
    host=await app.projects.history_host(project_id)
    await host.cloud_tasks.session(session_id)
    return project,host.cloud_tasks


@router.get('/cloud-profiles')
async def profiles(request:Request):
    manager=access(request);values=manager.book.public()
    for value in values:
        if value['profile_id'] in manager.connections.connections:value['connection']='connected'
    return values


@router.post('/cloud-sources')
async def source(request:Request,local_project_id:UUIDText):
    manager=access(request);value=await body(request,LocalSourceRequest)
    project,local=await domain(request,local_project_id,value.local_session_id)
    if not project.trusted:raise CloudError('cloud_project_trust_required',403)
    if not isinstance(project.source,LocalSource):raise CloudError('cloud_local_source_required')
    result=await local.capture(value.local_session_id,project.source.path,value.selection,manager.settings.input_limits,
        git=value.mode=='git',include=value.include,exclude=value.exclude)
    return JSONResponse(result,status_code=201)


@router.post('/cloud-tasks')
async def create(request:Request,local_project_id:UUIDText):
    manager=access(request);value=await body(request,ManualCreate)
    project,local=await domain(request,local_project_id,value.local_session_id)
    if not project.trusted:raise CloudError('cloud_project_trust_required',403)
    result=await manager.submit(local,project.name,value.model_dump(mode='json'))
    return command_response(request,result,status_code=202)


@router.get('/cloud-tasks/{binding_id}/history')
async def history(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
        thread_id:Identity|None=None,cursor:str|None=Query(default=None,max_length=16384),
        limit:int=Query(default=50,ge=1,le=100)):
    manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
    params={'limit':limit}
    if thread_id is not None:params['thread_id']=thread_id
    if cursor is not None:params['cursor']=cursor
    return await bounded_view(request,manager,local,binding_id,local_session_id,'history',params)


@router.get('/cloud-tasks/{binding_id}/operations')
async def task_operations(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId,
        offset:int=Query(default=0,ge=0,le=1000000),limit:int=Query(default=50,ge=1,le=100)):
    manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
    return command_response(request,await manager.commands.list(local,binding_id,local_session_id,offset=offset,limit=limit))


@router.get('/cloud-tasks/{binding_id}/draft')
async def draft(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
    access(request);_,local=await domain(request,local_project_id,local_session_id)
    with local.owner.operation():return command_response(request,await run_blocking(local.repo.commands.draft,binding_id,local_session_id))


@router.put('/cloud-tasks/{binding_id}/draft')
async def save_draft(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
    access(request);value=await body(request,TaskDraft);_,local=await domain(request,local_project_id,local_session_id)
    with local.owner.operation():return command_response(request,await run_blocking(local.repo.commands.save_draft,binding_id,local_session_id,value.model_dump(mode='json')))


@router.get('/cloud-tasks/{binding_id}/delete-preview')
async def delete_preview(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
    manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
    return command_response(request,await manager.commands.delete_preview(local,binding_id,local_session_id))


@router.post('/cloud-tasks/{binding_id}/delete')
async def delete_task(request:Request,binding_id:UUIDText,local_project_id:UUIDText):
    manager=access(request);value=await body(request,ManualDelete)
    _,local=await domain(request,local_project_id,value.local_session_id)
    return command_response(request,await manager.commands.submit(local,binding_id,'delete',value.model_dump(mode='json')),status_code=202)


async def bounded_view(request,manager,local,binding_id,session_id,view,params):
    value=await manager.view(local,binding_id,session_id,view,params)
    from naiyue_test_agent.web.serialization import encoded_size
    if encoded_size(value)>request.app.state.web_settings.max_frame_bytes:raise CloudError('cloud_response_too_large',502)
    return value


from .cloud_results import register as register_results
register_results(router,access,domain,bounded_view,body,command_response)

from .cloud_receives import register as register_receives
from .cloud_deliveries import register as register_deliveries
register_receives(router,access,domain,body,command_response)
register_deliveries(router,access,domain,body,command_response)

from .cloud_observation import register as register_observation
register_observation(router,access,domain,bounded_view)



@router.get('/cloud-tasks/{binding_id}/{view}')
async def observation(request:Request,binding_id:UUIDText,view:Literal['snapshot','usage','models'],
        local_project_id:UUIDText,local_session_id:SessionId,thread_id:Identity|None=None,
        after_model:Identity|None=None,limit:int=Query(default=50,ge=1,le=100)):
    manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
    if view in ('usage','models') and thread_id is not None or view in ('snapshot','models') and after_model is not None:
        raise CloudError('invalid_request',422)
    params={'limit':limit}
    if thread_id is not None:params['thread_id']=thread_id
    if after_model is not None:params['after_model']=after_model
    return await bounded_view(request,manager,local,binding_id,local_session_id,view,params)


@router.get('/cloud-tasks')
async def tasks(request:Request,local_project_id:UUIDText,local_session_id:SessionId,
                offset:int=Query(default=0,ge=0,le=1000000),limit:int=Query(default=50,ge=1,le=100)):
    access(request);_,local=await domain(request,local_project_id,local_session_id)
    with local.owner.operation():return await run_blocking(local.repo.list,local_session_id,offset=offset,limit=limit)


@router.get('/cloud-tasks/{binding_id}')
async def task(request:Request,binding_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
    manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
    return await manager.read(local,binding_id,local_session_id)


@router.get('/cloud-operations/{command_id}')
async def operation(request:Request,command_id:UUIDText,local_project_id:UUIDText,local_session_id:SessionId):
    manager=access(request);_,local=await domain(request,local_project_id,local_session_id)
    return command_response(request,await manager.commands.reconcile(local,command_id,local_session_id))


@router.post('/cloud-operations/{command_id}/{action}')
async def operate(request:Request,command_id:UUIDText,action:str,local_project_id:UUIDText,local_session_id:SessionId):
    manager=access(request)
    if action not in ('retry','cancel','reconcile'):raise CloudError('endpoint_not_found',404)
    await body(request,CloudRecord)
    _,local=await domain(request,local_project_id,local_session_id)
    if action=='reconcile':return command_response(request,await manager.commands.reconcile(local,command_id,local_session_id))
    result=await (manager.retry(local,command_id,local_session_id) if action=='retry'
        else manager.cancel(local,command_id,local_session_id))
    return command_response(request,result,status_code=202)


@router.post('/cloud-tasks/{binding_id}/messages')
async def message(request:Request,binding_id:UUIDText,local_project_id:UUIDText):
    manager=access(request);value=await body(request,ManualMessage)
    _,local=await domain(request,local_project_id,value.local_session_id)
    return command_response(request,await manager.commands.submit(local,binding_id,'message',value.model_dump(mode='json')),status_code=202)


@router.post('/cloud-tasks/{binding_id}/control')
async def control(request:Request,binding_id:UUIDText,local_project_id:UUIDText):
    manager=access(request);value=await body(request,ManualControl)
    _,local=await domain(request,local_project_id,value.local_session_id)
    return command_response(request,await manager.commands.submit(local,binding_id,value.kind,value.model_dump(mode='json')),status_code=202)


@router.post('/cloud-tasks/{binding_id}/environment')
async def environment(request:Request,binding_id:UUIDText,local_project_id:UUIDText):
    manager=access(request);value=await body(request,ManualEnvironment)
    _,local=await domain(request,local_project_id,value.local_session_id)
    return command_response(request,await manager.commands.submit(local,binding_id,value.action,value.model_dump(mode='json')),status_code=202)
