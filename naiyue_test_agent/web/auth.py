"""Local authorization is visible in the API boundary, independent of tools."""

import secrets
from .errors import WebError


def authorize(request, settings):
    if request.headers.get('host') != settings.authority:
        raise WebError('invalid_host', 403)
    provided = request.headers.get('authorization', '')
    expected = 'Bearer ' + settings.access_token
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise WebError('unauthorized', 401)
    origin = request.headers.get('origin')
    if (origin is not None and origin != settings.origin) or (
            request.method not in ('GET', 'HEAD') and origin != settings.origin):
        raise WebError('invalid_origin', 403)
