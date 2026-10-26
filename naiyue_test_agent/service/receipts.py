"""Bounded command receipts; an accepted task outlives its HTTP caller."""
import asyncio
from hashlib import sha256
import json
import logging
from uuid import UUID

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def _error(code, status=409):
    return JSONResponse({'error': {'code': code}}, status_code=status)


class CommandReceipts:
    def __init__(self, options):
        self.options = options
        self.entries = {}
        self.closing = False
        self.bytes = 0
        self.reserved = 0
        self.scopes = {}

    async def forget_scope(self, identity):
        pending = [(key, self.entries[key][1]) for key, scope in self.scopes.items() if scope == identity]
        for key, task in pending:
            if self.entries.get(key, (None, None))[1] is not task:
                continue
            _, _, body = await asyncio.shield(task)
            if self.entries.get(key, (None, None))[1] is task:
                self.bytes -= len(body)
                del self.entries[key], self.scopes[key]

    async def drain(self):
        tasks = [task for _, task in self.entries.values()]
        if tasks:
            await asyncio.gather(*tasks)

    async def execute(self, key, fingerprint, operation, *, scope_id=None):
        if self.closing:
            return 503, [], _error('service_closing', 503).body
        previous = self.entries.get(key)
        if previous is not None:
            if previous[0] != fingerprint:
                return 409, [], _error('request_id_conflict').body
            return await asyncio.shield(previous[1])
        if (len(self.entries) >= self.options.max_receipts
                or self.bytes + self.reserved + self.options.max_frame_bytes > self.options.receipt_cache_bytes):
            return 409, [], _error('request_window_full').body
        self.reserved += self.options.max_frame_bytes
        task = asyncio.create_task(self._produce(key, operation))
        self.entries[key] = fingerprint, task
        self.scopes[key] = scope_id
        return await asyncio.shield(task)

    async def _produce(self, key, operation):
        try:
            status, headers, body = await operation()
            # Only a confirmed pre-execution rejection releases its command ID.
            # Cleanup belongs to the producer even if its HTTP caller disconnects.
            try:
                code = json.loads(body).get('error', {}).get('code')
            except (ValueError, AttributeError):
                code = None
            if code in ('invalid_request', 'stale_approval', 'stale_run', 'session_busy', 'run_capacity_exceeded'):
                if self.entries.get(key, (None, None))[1] is asyncio.current_task():
                    del self.entries[key], self.scopes[key]
            else:
                self.bytes += len(body)
            return status, headers, body
        except Exception as error:
            logger.error('service_command_failed error_type=%s', type(error).__name__)
            body = _error('operation_failed', 500).body
            self.bytes += len(body)
            return 500, [], body
        finally:
            self.reserved -= self.options.max_frame_bytes


class IdempotentCommands:
    def __init__(self, app, *, owner):
        self.app, self.owner = app, owner

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if (scope['type'] != 'http' or scope['method'] in ('GET', 'HEAD', 'OPTIONS')
                or not path.startswith('/api/v1/')):
            return await self.app(scope, receive, send)
        # Authentication precedes buffering or reserving untrusted command IDs.
        from starlette.requests import Request
        from naiyue_test_agent.web.auth import authorize
        from naiyue_test_agent.web.errors import WebError
        try:
            authorize(Request(scope), self.owner.state.web_settings)
        except WebError as error:
            return await _error(error.code, error.status)(scope, receive, send)
        receipts = self.owner.state.receipts
        parts = path.split('/')
        scope_id = parts[4] if len(parts) > 4 and parts[3] == 'scopes' else None
        if scope_id is not None and scope_id.startswith('temp-'):
            try:
                self.owner.state.temporary.require(scope_id, active=True)
            except WebError as error:
                return await _error(error.code, error.status)(scope, receive, send)
        headers = dict(scope['headers'])
        handshake = path == '/api/v1/handshake'
        try:
            if not handshake:
                value = headers.get(b'x-request-id', b'').decode('ascii')
                key = str(UUID(value))
                if key != value.lower():
                    raise ValueError('invalid_id')
            chunks, size = [], 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                part = message.get('body', b'')
                size += len(part)
                if size > receipts.options.max_frame_bytes:
                    return await _error('request_too_large', 413)(scope, receive, send)
                chunks.append(part)
                if not message.get('more_body', False):
                    break
            body = b''.join(chunks)
            if handshake:
                supplied = False
                async def handshake_receive():
                    nonlocal supplied
                    if not supplied:
                        supplied = True
                        return {'type': 'http.request', 'body': body, 'more_body': False}
                    return await receive()
                return await self.app(scope, handshake_receive, send)
            data = json.loads(body) if body else None
            if isinstance(data, dict) and 'request_id' in data:
                if not isinstance(data['request_id'], str) or str(UUID(data['request_id'])) != key:
                    raise ValueError('inconsistent_id')
            fingerprint = sha256(json.dumps([scope['method'], path,
                scope.get('query_string', b'').decode('ascii'), data],
                sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return await _error('invalid_request_id', 422)(scope, receive, send)

        async def operation():
            response, response_headers, response_size = [], [], 0
            status, supplied, oversized = 500, False, False
            async def buffered_receive():
                nonlocal supplied
                if not supplied:
                    supplied = True
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                await asyncio.Event().wait()
            async def capture(message):
                nonlocal status, response_size, oversized
                if message['type'] == 'http.response.start':
                    status = message['status']
                    response_headers.extend((k, v) for k, v in message['headers']
                        if k.lower() not in (b'content-length', b'content-type'))
                elif message['type'] == 'http.response.body':
                    part = message.get('body', b'')
                    response_size += len(part)
                    if response_size > receipts.options.max_frame_bytes:
                        oversized = True
                        response.clear()
                    elif not oversized:
                        response.append(part)
            await self.app(scope, buffered_receive, capture)
            if oversized:
                logger.warning('service_command_result_omitted reason=frame_limit')
                return 503, [], _error('command_result_unavailable', 503).body
            return status, response_headers, b''.join(response)

        if key in receipts.entries and scope_id is not None and len(parts) > 6:
            service = self.owner.state.scopes.services.get(scope_id)
            if parts[5] == 'runs':
                if service is None or parts[6] not in service.delivery.runs:
                    return await _error('run_not_found', 404)(scope, receive, send)
            elif parts[5] == 'sessions' and scope['method'] != 'DELETE':
                if service is None:
                    return await _error('session_not_found', 404)(scope, receive, send)
                try:
                    await service.application.sessions.status(parts[6])
                except Exception as error:
                    from naiyue_test_agent.web.errors import public_error
                    logger.info('service_receipt_scope_unavailable error_type=%s', type(error).__name__)
                    failure = public_error(error)
                    return await _error(failure.code, failure.status)(scope, receive, send)
        status, result_headers, content = await receipts.execute(key, fingerprint, operation, scope_id=scope_id)
        await send({'type': 'http.response.start', 'status': status, 'headers': [
            *result_headers, (b'content-type', b'application/json'),
            (b'content-length', str(len(content)).encode())]})
        await send({'type': 'http.response.body', 'body': content})
