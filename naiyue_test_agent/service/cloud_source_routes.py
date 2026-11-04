"""Authenticated project-scoped discovery and read-only budget preview."""
from fastapi import Request,Query
from naiyue_test_agent.agent_runtime.async_utils import run_blocking
from naiyue_test_agent.contracts.cloud_tasks import CloudError,UUIDText
from naiyue_test_agent.contracts.cloud_local_tasks import LocalSourceRequest,SessionId
from naiyue_test_agent.contracts.projects import LocalSource
from naiyue_test_agent.cloud_files.source_scope import tree,preview_scope


def register(router,access,domain,body):
    async def root(request,project_id,session_id):
        manager=access(request);project,local=await domain(request,project_id,session_id)
        if not project.trusted:raise CloudError('cloud_project_trust_required',403)
        if not isinstance(project.source,LocalSource):raise CloudError('cloud_local_source_required')
        if local.authority:
            execution=await run_blocking(local.authority.resolve,project_id)
            if execution.path!=project.source.path:raise CloudError('cloud_source_identity_changed')
        return manager,project.source.path,local

    @router.get('/cloud-source-tree')
    async def browse(request:Request,local_project_id:UUIDText,local_session_id:SessionId,
            path:str=Query(default='',max_length=1024),offset:int=Query(default=0,ge=0,le=500000)):
        manager,path_root,local=await root(request,local_project_id,local_session_id)
        with local.owner.operation():return await run_blocking(tree,path_root,manager.settings.input_limits,path=path,offset=offset)

    @router.post('/cloud-source-preview')
    async def preview(request:Request,local_project_id:UUIDText):
        access(request);value=await body(request,LocalSourceRequest)
        manager,path_root,local=await root(request,local_project_id,value.local_session_id)
        paths=value.paths if value.paths is not None else value.selection.get('paths',[]) if value.selection else []
        with local.owner.operation():return await run_blocking(preview_scope,path_root,manager.settings.input_limits,
            mode=value.mode,paths=paths,include=value.include,exclude=value.exclude)
