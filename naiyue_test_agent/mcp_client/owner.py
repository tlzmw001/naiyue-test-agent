"""One task enters, holds and exits an SDK connection; callers borrow a lease."""

import asyncio
from contextvars import Context
from dataclasses import dataclass, field
import logging

from ..agent_runtime.async_utils import await_settled


logger = logging.getLogger(__name__)


class MCPResourceError(RuntimeError):
    """Locally selected safe resource failure, without protocol/credential bodies."""

    retryable = False


@dataclass(frozen=True)
class MCPLease:
    client: object = field(repr=False)
    catalog: object
    config_identity: str


def exception_leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [leaf for member in error.exceptions for leaf in exception_leaves(member)]
    return [error]


def is_retryable_mcp_error(error):
    from .connections import MCPResponsePolicyError
    from ..tools.retry import is_transient_error
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(is_retryable_mcp_error(item) for item in error.exceptions)
    return not isinstance(error, MCPResponsePolicyError) and is_transient_error(error)


class ConnectionOwner:
    def __init__(self, server_id, settings, network, environment, identity):
        self.server_id, self.settings, self.network = server_id, settings, network
        self.environment, self.identity = environment, identity
        self.ready = asyncio.get_running_loop().create_future()
        # Creation can fail after the last waiter was cancelled. Retrieve the
        # future's exception even then; close() still checks the owner's failure.
        self.ready.add_done_callback(lambda value: None if value.cancelled() else value.exception())
        self.stop = asyncio.Event()
        self.users = set()
        self.waiters = set()
        self.invalid = False
        self.retiring = False
        self.observed = []
        self.failure = None
        self.startup_failed = False
        self.stopping = False
        self.close_error = None
        self.task = asyncio.create_task(self._own(), name='mcp-connection-owner', context=Context())

    async def _own(self):
        from .connections import open_connection
        from .discovery import read_catalog
        try:
            async with asyncio.timeout(self.settings.connect_timeout_seconds) as deadline:
                async with open_connection(self.settings, self.network, environment=self.environment) as client:
                    catalog = await read_catalog(self.server_id, self.settings, client.session)
                    deadline.reschedule(None)
                    self.ready.set_result(MCPLease(client, catalog, self.identity))
                    await self.stop.wait()
        except BaseException as error:
            self.failure = error
            expected_cancel = self.stopping and all(isinstance(e, asyncio.CancelledError)
                                                   for e in exception_leaves(error))
            if not expected_cancel:
                logger.error('mcp_owner_failed server=%s error_type=%s', self.server_id, type(error).__name__)
            if not self.ready.done():
                self.startup_failed = True
                code = 'mcp_connect_timeout' if isinstance(error, TimeoutError) else 'mcp_connect_failed'
                failure = MCPResourceError(code)
                failure.retryable = is_retryable_mcp_error(error)
                self.ready.set_exception(failure)
        finally:
            self.invalid = True
            if not self.ready.done():
                self.ready.set_exception(MCPResourceError('mcp_connection_closed'))

    async def close(self):
        self.stopping = True
        self.invalid = True
        if not self.ready.done():
            self.task.cancel()
        else:
            self.stop.set()
        try:
            await await_settled(self.task)
        except asyncio.CancelledError:
            if not self.task.cancelled():
                raise
            # A task cancelled before its first tick never entered _own/finally.
            if not self.ready.done():
                self.ready.set_exception(MCPResourceError('mcp_connection_closed'))
        if self.failure is None or self.startup_failed:
            return
        leaves = exception_leaves(self.failure)
        if all(isinstance(leaf, asyncio.CancelledError) or any(leaf is known for known in self.observed)
               for leaf in leaves):
            if self.observed:
                logger.info('mcp_cleanup_repeated_failure server=%s', self.server_id)
            return
        if self.close_error is None:
            self.close_error = MCPResourceError('mcp_cleanup_failed')
        raise self.close_error from None
