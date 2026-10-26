"""A request-owned lease stops and destroys a RAM host when its connection ends."""
import asyncio
from dataclasses import dataclass, field
import logging
import json
from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import StreamingResponse

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.execution_roots import ExecutionRoot, validate_execution_root
from naiyue_test_agent.web.errors import WebError, public_error

logger = logging.getLogger(__name__)


class TemporaryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    root: Path | None = None


@dataclass
class Ticket:
    root: Path | None
    state: str = 'pending'
    end: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    close_task: asyncio.Task | None = None
    cleanup_failed: bool = False


class TemporaryScopes:
    def __init__(self, scopes, receipts):
        self.scopes, self.receipts = scopes, receipts
        self.tickets = {}

    def create(self, root):
        if len(self.tickets) >= self.receipts.options.max_receipts:
            raise WebError('temporary_window_full')
        if root is not None:
            root = root.expanduser().resolve()
            validate_execution_root(ExecutionRoot(root))
        identity = 'temp-' + str(uuid4())
        self.tickets[identity] = Ticket(root)
        return {'scope_id': identity}

    def require(self, identity, *, active=False):
        ticket = self.tickets.get(identity)
        if ticket is None:
            raise WebError('temporary_scope_not_found', 404)
        if ticket.state in ('closing', 'closed'):
            raise WebError('temporary_scope_closed')
        if active and ticket.state != 'active':
            raise WebError('temporary_lease_required')
        return ticket

    def reserve(self, identity):
        ticket = self.require(identity)
        if ticket.state != 'pending':
            raise WebError('temporary_lease_in_use')
        ticket.state = 'active'
        return LeaseResponse(self, identity, ticket)

    async def close(self, identity):
        ticket = self.tickets.get(identity)
        if ticket is None:
            raise WebError('temporary_scope_not_found', 404)
        if ticket.closed.is_set():
            if ticket.cleanup_failed:
                raise WebError('temporary_cleanup_unconfirmed')
            return
        if ticket.close_task is None:
            ticket.state = 'closing'
            ticket.end.set()
            ticket.close_task = asyncio.create_task(self._close(identity, ticket))
            ticket.close_task.add_done_callback(lambda task: setattr(ticket, 'close_task', None))
        await await_settled(ticket.close_task)

    async def _close(self, identity, ticket):
        first = None
        async def seal():
            service = self.scopes.services.get(identity)
            if service is not None:
                await service.delivery.seal()
        async def stop():
            await self.scopes.application.temporary.close(identity)
        async def drain():
            service = self.scopes.services.pop(identity, None)
            if service is not None:
                await service.delivery.close(close_application=False)
        async def close_host():
            nonlocal first
            # Cancel this lease's assembly without waiting for unrelated scopes.
            await self.scopes.cancel_open(identity)
            for cleanup in (seal, stop, drain):
                try:
                    await cleanup()
                except BaseException as error:
                    logger.error('temporary_cleanup_failed error_type=%s', type(error).__name__)
                    if first is None:
                        first = error
        for cleanup in (close_host, lambda: self.receipts.forget_scope(identity)):
            try:
                await cleanup()
            except BaseException as error:
                logger.error('temporary_cleanup_failed error_type=%s', type(error).__name__)
                if first is None:
                    first = error
        for key, (scope_id, _) in tuple(self.scopes.output_plans.items()):
            if scope_id == identity:
                del self.scopes.output_plans[key]
        ticket.state = 'closed'
        ticket.cleanup_failed = first is not None
        ticket.closed.set()
        if first is not None:
            raise first

    async def shutdown(self):
        results = await asyncio.gather(*(self.close(identity) for identity in tuple(self.tickets)),
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result


class LeaseResponse(StreamingResponse):
    def __init__(self, manager, identity, ticket):
        self.manager, self.identity = manager, identity
        async def stream():
            opening = asyncio.create_task(manager.scopes.get(identity))
            ending = asyncio.create_task(ticket.end.wait())
            try:
                done, _ = await asyncio.wait((opening, ending), return_when=asyncio.FIRST_COMPLETED)
                if ending in done:
                    return
                try:
                    await opening
                except Exception as error:
                    logger.error('temporary_open_failed error_type=%s', type(error).__name__)
                    code = public_error(error).code
                    yield ('event: error\ndata: ' + json.dumps({'error': {'code': code}}) + '\n\n').encode()
                    return
                yield b'event: ready\ndata: {"ready":true}\n\n'
                await ending
            finally:
                with anyio.CancelScope(shield=True):
                    for task in (opening, ending):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(opening, ending, return_exceptions=True)
        super().__init__(stream(), media_type='text/event-stream',
            headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    async def __call__(self, scope, receive, send):
        primary = None
        try:
            await super().__call__(scope, receive, send)
        except BaseException as error:
            primary = error
            raise
        finally:
            # ASGI send may fail outside the generator, and AnyIO cancellation
            # can repeat at every await. The request owns this explicit cleanup.
            with anyio.CancelScope(shield=True):
                for cleanup in (lambda: self.manager.close(self.identity), self.body_iterator.aclose):
                    try:
                        await cleanup()
                    except BaseException as error:
                        if primary is None:
                            primary = error
                        else:
                            logger.error('lease_cleanup_secondary_failed error_type=%s', type(error).__name__)
                if primary is not None:
                    raise primary


router = APIRouter(prefix='/api/v1/temporary')


@router.post('')
async def create(body: TemporaryInput, request: Request):
    return request.app.state.temporary.create(body.root)


@router.get('/{identity}/lease')
async def lease(identity: str, request: Request):
    return request.app.state.temporary.reserve(identity)


@router.delete('/{identity}')
async def close(identity: str, request: Request):
    await request.app.state.temporary.close(identity)
    return {'closed': True}
