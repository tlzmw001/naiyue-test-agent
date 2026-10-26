"""An HTTP/SSE client holds no graph, database, or service shutdown ownership."""
import json
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from .ownership import read_discovery, service_running
from .settings import ServiceError, ServiceIdentity, StartupFailure


def _path(path):
    parsed = urlsplit(path)
    if (not path.startswith('/api/v1/') or parsed.scheme or parsed.netloc or parsed.fragment
            or '\\' in path or any(part in ('.', '..') for part in parsed.path.split('/'))):
        raise ServiceError('invalid_service_path')
    return path


def _failure(status, raw):
    if status < 400:
        return
    code = raw.get('error', {}).get('code') if isinstance(raw, dict) else None
    if not isinstance(code, str) or re.fullmatch(r'[a-z_]{1,80}', code) is None:
        code = 'service_request_failed'
    reason = raw.get('error', {}).get('reason') if isinstance(raw, dict) else None
    if not isinstance(reason, str) or re.fullmatch(r'[a-z_]{1,80}', reason) is None:
        reason = None
    startup = raw.get('error', {}).get('startup') if isinstance(raw, dict) else None
    if startup is not None:
        try:
            startup = StartupFailure.model_validate(startup)
        except ValueError:
            raise ServiceError('service_response_invalid') from None
    raise ServiceError(code, reason=reason, startup=startup)


class ServiceClient:
    def __init__(self, options, record, http):
        self.options, self._record, self._http = options, record, http
        self.instance_id = record.instance_id

    @classmethod
    async def connect(cls, options, *, transport=None):
        if not service_running(options):
            raise ServiceError('service_unavailable')
        record = read_discovery(options)
        if record is None:
            raise ServiceError('service_unavailable')
        if options.port and record.origin != options.origin:
            raise ServiceError('service_endpoint_mismatch')
        http = httpx.AsyncClient(base_url=record.origin, transport=transport, trust_env=False,
            follow_redirects=False, timeout=options.request_timeout,
            headers={'Authorization': f'Bearer {record.access_token}', 'Origin': record.origin,
                     'X-Service-Instance': record.instance_id})
        client = cls(options, record, http)
        try:
            result = await client.request('POST', '/api/v1/handshake', json=options.identity.model_dump(mode='json'))
            actual = ServiceIdentity.model_validate(result['identity'])
            options.identity.check(actual)
            record.identity.check(actual)
            if result['instance_id'] != record.instance_id:
                raise ServiceError('service_instance_mismatch')
            return client
        except BaseException:
            await http.aclose()
            raise

    async def _json(self, response):
        parts, size = [], 0
        async for part in response.aiter_bytes():
            size += len(part)
            if size > self.options.max_frame_bytes:
                raise ServiceError('service_response_too_large')
            parts.append(part)
        try:
            value = json.loads(b''.join(parts))
        except (ValueError, UnicodeError):
            raise ServiceError('service_response_invalid') from None
        _failure(response.status_code, value)
        return value

    async def request(self, method, path, *, json=None, request_id=None):
        path = _path(path)
        method = method.upper()
        headers = {}
        if method not in ('GET', 'HEAD', 'OPTIONS') and path != '/api/v1/handshake':
            identity = request_id or (json.get('request_id') if isinstance(json, dict) else None) or str(uuid4())
            try:
                headers['X-Request-ID'] = str(UUID(identity))
            except (ValueError, TypeError, AttributeError):
                raise ServiceError('invalid_request_id') from None
        try:
            async with self._http.stream(method, path, json=json, headers=headers) as response:
                if 300 <= response.status_code < 400:
                    raise ServiceError('service_redirect_refused')
                return await self._json(response)
        except httpx.HTTPError:
            raise ServiceError('service_connection_failed') from None

    async def events(self, scope_id, run_id, *, after=0):
        try:
            identity = scope_id.removeprefix('temp-')
            if scope_id != 'chats' and str(UUID(identity)) != identity:
                raise ValueError('scope')
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', run_id) or type(after) is not int or after < 0:
                raise ValueError('cursor')
        except (ValueError, TypeError, AttributeError):
            raise ServiceError('invalid_service_path') from None
        path = f'/api/v1/scopes/{scope_id}/runs/{run_id}/events?after={after}'
        try:
            async with self._http.stream('GET', path, timeout=None) as response:
                if response.status_code != 200:
                    await self._json(response)
                    raise ServiceError('service_stream_unavailable')
                pending = b''
                async for chunk in response.aiter_bytes():
                    pending += chunk
                    pending = pending.replace(b'\r\n', b'\n')
                    while b'\n\n' in pending:
                        frame, pending = pending.split(b'\n\n', 1)
                        if len(frame) > self.options.max_frame_bytes + 1024:
                            raise ServiceError('service_response_too_large')
                        event = self._event(frame)
                        if event is not None:
                            yield event
                    if len(pending) > self.options.max_frame_bytes + 1024:
                        raise ServiceError('service_response_too_large')
        except httpx.HTTPError:
            raise ServiceError('service_connection_failed') from None

    @staticmethod
    def _event(frame):
        event, data = {}, []
        try:
            for line in frame.decode('utf-8').splitlines():
                field, separator, value = line.partition(':')
                if not separator:
                    continue
                value = value.removeprefix(' ')
                if field in ('event', 'id'):
                    event[field] = value
                elif field == 'data':
                    data.append(value)
            if data:
                return {**event, 'data': json.loads('\n'.join(data))}
        except (ValueError, UnicodeError):
            raise ServiceError('service_response_invalid') from None

    async def aclose(self):
        await self._http.aclose()
