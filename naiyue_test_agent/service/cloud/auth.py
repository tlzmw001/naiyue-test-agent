"""Visible boundary checks; SSH and listener separation do not replace auth."""
from naiyue_test_agent.contracts.cloud_tasks import CloudError
from naiyue_test_agent.web.auth import authorize
from naiyue_test_agent.web.errors import WebError


def owner_authorize(request, runtime, *, query_fields=()):
    for header in ('host', 'authorization', 'origin', 'x-service-instance', 'x-cloud-protocol'):
        if len(request.headers.getlist(header)) > 1:
            raise CloudError('invalid_headers', 400)
    try:
        authorize(request, runtime.settings)
    except WebError as error:
        raise CloudError(error.code, error.status) from None
    if (set(request.query_params) - set(query_fields)
            or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)):
        raise CloudError('invalid_request', 422)
    instance = request.headers.get('x-service-instance')
    if instance is not None and instance != runtime.instance_id:
        raise CloudError('cloud_instance_mismatch')
    protocol = request.headers.get('x-cloud-protocol')
    if ((protocol is not None and protocol != '1')
            or (request.method not in ('GET', 'HEAD') and protocol != '1')):
        raise CloudError('cloud_protocol_mismatch')


def task_bearer(request, runtime):
    if request.headers.get('host') != runtime.settings.task_listen:
        raise CloudError('invalid_host', 403)
    if len(request.headers.getlist('authorization')) != 1 or len(request.headers.getlist('host')) != 1:
        raise CloudError('unauthorized', 401)
    value = request.headers.get('authorization', '')
    if not value.startswith('Bearer '):
        raise CloudError('unauthorized', 401)
    if request.query_params:
        raise CloudError('invalid_request', 422)
    return value[7:]
