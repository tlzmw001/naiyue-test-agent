"""Management deletion is available only on the explicitly authenticated owner app."""
from fastapi import Request
from starlette.responses import JSONResponse
from .auth import owner_authorize
from .http import _body
from naiyue_test_agent.application.cloud.work import cloud_work


def register_delete_routes(owner, runtime):
    @owner.get('/api/cloud/v1/tasks/{task_id}/delete-preview')
    async def preview(request: Request, task_id: str):
        owner_authorize(request, runtime)
        return await cloud_work(runtime, lambda: runtime.deletions.preview(task_id))

    @owner.post('/api/cloud/v1/tasks/{task_id}/delete')
    async def delete(request: Request, task_id: str):
        owner_authorize(request, runtime)
        receipt = await runtime.deletions.command(task_id, await _body(request, runtime))
        return JSONResponse(receipt.model_dump(), status_code=receipt.http_status)
