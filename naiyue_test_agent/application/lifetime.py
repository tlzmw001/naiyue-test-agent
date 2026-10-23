"""One event loop, admission lifetime and ordered resource shutdown."""
import asyncio
import logging
from contextlib import contextmanager

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
from naiyue_test_agent.contracts.failures import safe_error_type

logger = logging.getLogger(__name__)


class ApplicationLifetime:
    def __init__(self, catalog, sessions, resources):
        self._catalog, self._sessions, self._resources = catalog, sessions, resources
        self._loop = asyncio.get_running_loop()
        self._tasks, self._operations = set(), {}
        self._close_task = None
        self._closed = self._closing = False

    def check_open(self):
        if (asyncio.get_running_loop() is not self._loop or self._closed
                or self._closing and asyncio.current_task() not in self._operations.values()):
            raise RuntimeError('Async application must be open on its owning event loop')

    @contextmanager
    def operation(self):
        """Track operation settlement, without owning the caller's later work."""
        self.check_open()
        done = self._loop.create_future()
        self._operations[done] = asyncio.current_task()
        try:
            yield
        finally:
            self._operations.pop(done)
            done.set_result(None)

    def background_done(self, task):
        self._tasks.discard(task)
        if task.cancelled():
            logger.error('background_run_failed error_type=CancelledError')
        else:
            error = task.exception()
            if error is not None:
                logger.error('background_run_failed error_type=%s', safe_error_type(error))

    async def _close_runtime(self):
        primary = None
        try:
            self._sessions.stop_loaded_sessions()
            background_error = None
            try:
                if self._sessions.memory_jobs is not None:await self._sessions.memory_jobs.aclose()
            except BaseException as error:
                background_error = error
            try:
                await self._sessions.background_tasks.aclose()
            except BaseException as error:
                if background_error is None:background_error = error
                else:logger.error('process_cleanup_secondary_failed error_type=%s',safe_error_type(error))
            # Nested operations are covered by their outer operation. No new ingress
            # can start after closing; these signals settle only after required IO.
            try:
                if self._sessions.mcp_resources is not None:
                    await self._sessions.mcp_resources.aclose()
            except BaseException as error:
                if background_error is None:
                    raise
                logger.error('mcp_cleanup_secondary_failed error_type=%s', safe_error_type(error))
            finally:
                await asyncio.gather(*tuple(self._operations))
                results = await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        logger.error('owned_run_failed error_type=%s', safe_error_type(result))
            if background_error is not None:
                raise background_error
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await self._catalog.aclose()
            except BaseException as error:
                if primary is None:
                    raise
                logger.error('model_cleanup_secondary_failed error_type=%s', safe_error_type(error))
            finally:
                self._closed = True

    async def _close_resources(self):
        primary = None
        try:
            await self._close_runtime()
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await self._resources.aclose()
            except BaseException as error:
                if primary is None:
                    raise
                logger.error('application_storage_cleanup_secondary_failed error_type=%s', type(error).__name__)

    async def aclose(self):
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError('Async application must close on its owning event loop')
        services = current_run_scope()
        if services is not None and services.observation.publisher is not None:
            raise RuntimeError('Cannot close the application from an active run')
        if asyncio.current_task() in self._operations.values() or asyncio.current_task() in self._tasks:
            raise RuntimeError('Cannot close the application from an active operation')
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close_resources())
        await await_settled(self._close_task)
