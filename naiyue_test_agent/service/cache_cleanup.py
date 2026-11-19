"""Authenticated service-wide cache maintenance."""
import json

from fastapi import APIRouter, Request

from naiyue_test_agent.application.projects.cache_cleanup_operation import CacheCleanupBusy, CacheCleanupFrameTooSmall
from naiyue_test_agent.contracts.cache_cleanup import CleanupRequest, PATH
from naiyue_test_agent.web.auth import authorize
from naiyue_test_agent.web.errors import WebError

router = APIRouter(prefix=PATH)


def authorize_maintenance(request, app):
    authorize(request, app.state.web_settings)
    if request.headers.get('x-service-instance') != app.state.owner.record.instance_id:
        raise WebError('service_instance_mismatch', 409)


def maintenance(request):
    authorize_maintenance(request, request.app)
    return request.app.state.application.cache_cleanup


@router.get('')
async def policies(request: Request):
    return maintenance(request).policies()


@router.post('')
async def execute(body: CleanupRequest, request: Request):
    cleanup = maintenance(request)
    if body.request_id != request.headers.get('x-request-id'):
        raise WebError('invalid_request_id', 422)
    available = {entry['id'] for entry in cleanup.policies()['categories'] if entry['available']}
    if not set(body.categories) <= available:
        raise WebError('cache_cleanup_category_unavailable', 422)
    try:
        return await cleanup.execute(body.request_id, body.categories,
                                     result_limit_bytes=request.app.state.receipts.options.max_frame_bytes,
                                     service_closing=lambda: request.app.state.scopes.closing)
    except CacheCleanupBusy as error:
        raise WebError('cache_cleanup_busy') from error
    except CacheCleanupFrameTooSmall as error:
        raise WebError('cache_cleanup_result_budget', 503) from error


@router.get('/{request_id}')
async def lookup(request: Request, request_id: str):
    maintenance(request)
    task = request.app.state.receipts.lookup(request_id, ('POST', PATH))
    if task is None:
        raise WebError('cache_cleanup_request_not_found', 404)
    if not task.done():
        return dict(state='running', request_id=request_id)
    status, _, body = task.result()
    kind = 'result' if status == 200 else 'error'
    decoded = json.loads(body)
    return dict(state='finished', request_id=request_id, http_status=status,
                **{kind: decoded if kind == 'result' else decoded.get('error', decoded)})
