"""Owner-only archive endpoints; task bearer cannot read them."""
from fastapi import Request
from .auth import owner_authorize
from naiyue_test_agent.application.cloud.work import cloud_work


def register_history_routes(owner,runtime):
    base='/api/cloud/v1/tasks/{task_id}/history'

    @owner.get(base)
    async def page(request:Request,task_id:str,thread_id:str|None=None,limit:int|None=None,cursor:str|None=None):
        owner_authorize(request,runtime,query_fields=('thread_id','limit','cursor'))
        return await cloud_work(runtime,lambda:runtime.archive.page(task_id,thread_id=thread_id,limit=limit,cursor=cursor))

    @owner.get(base+'/threads')
    async def threads(request:Request,task_id:str,limit:int|None=None,cursor:str|None=None):
        owner_authorize(request,runtime,query_fields=('limit','cursor'))
        return await cloud_work(runtime,lambda:runtime.archive.threads(task_id,limit=limit,cursor=cursor))

    @owner.get(base+'/messages/{message_id}')
    async def detail(request:Request,task_id:str,message_id:str,thread_id:str,revision:int,offset:int=0,limit:int|None=None,field:str='content'):
        owner_authorize(request,runtime,query_fields=('thread_id','revision','offset','limit','field'))
        return await cloud_work(runtime,lambda:runtime.archive.detail(task_id,message_id,thread_id=thread_id,
            revision=revision,offset=offset,limit=limit,field=field))
