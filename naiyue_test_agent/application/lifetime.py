"""One event loop, admission lifetime and ordered resource shutdown."""
import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager

from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.agent_runtime.run_scope import current_run_scope
from naiyue_test_agent.contracts.failures import safe_error_type

logger = logging.getLogger(__name__)


class ApplicationLifetime:
    def __init__(self, catalog, sessions, resources, *, parent=None):
        self._catalog, self._sessions, self._resources = catalog, sessions, resources
        self._parent, self._children = parent, []
        self._loop = asyncio.get_running_loop()
        self._tasks, self._operations = set(), {}
        self._opening_tasks = set()
        self._close_task = None
        self._closed = self._closing = False
        self._environments = None

    def own_environments(self,environments):
        self.check_open()
        if self._environments is not None:raise RuntimeError('Environment owner is already attached')
        self._environments=environments

    def check_open(self):
        task = asyncio.current_task()
        accepted = (task in self._operations.values() or
                    any(task in child._operations.values() for child in self._children))
        if (asyncio.get_running_loop() is not self._loop or self._closed
                or self._closing and not accepted):
            raise RuntimeError('Async application must be open on its owning event loop')
        if self._parent is not None:
            self._parent.check_open()

    def check_controls_open(self):
        """Thread-safe controls are also called by synchronous model SDK workers."""
        if self._closed or self._parent is not None and self._parent._closed:
            raise RuntimeError('Async application must be open on its owning event loop')

    def check_admission_open(self):
        self.check_open()
        if self._closing or self._closed:
            raise RuntimeError('Cannot start a run while its host is closing')
        if self._parent is not None:
            self._parent.check_admission_open()

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
        if self._sessions is None:
            return await self._close_application_runtime()
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
            self._sessions._admission.release(self._sessions)
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                if self._catalog is not None:
                    await self._catalog.aclose()
            except BaseException as error:
                if primary is None:
                    raise
                logger.error('model_cleanup_secondary_failed error_type=%s', safe_error_type(error))
            finally:
                self._closed = True

    async def _close_application_runtime(self):
        primary = None
        try:
            opening = tuple(self._opening_tasks)
            for task in opening:
                task.cancel()
            await asyncio.gather(*opening, return_exceptions=True)
            for child in self._children:
                child._sessions.stop_loaded_sessions()
            if self._environments is not None:
                try:await self._environments.aclose()
                except BaseException as error:
                    if primary is None:primary=error
                    else:logger.error('environment_cleanup_secondary_failed error_type=%s',safe_error_type(error))
            await asyncio.gather(*tuple(self._operations))
            results = await asyncio.gather(*(child._close_runtime() for child in tuple(self._children)),
                                           return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    if primary is None:
                        primary = result
                    else:
                        logger.error('host_cleanup_secondary_failed error_type=%s', safe_error_type(result))
            if primary is not None:
                raise primary
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
        if (asyncio.current_task() in self._operations.values() or asyncio.current_task() in self._tasks
                or any(asyncio.current_task() in child._operations.values() or
                       asyncio.current_task() in child._tasks for child in self._children)):
            raise RuntimeError('Cannot close the application from an active operation')
        if self._closed and self._close_task is None:
            return
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close_resources())
        await await_settled(self._close_task)


def _register_cleanup(stack, name, close):
    """Keep the first failure while still closing every acquired resource."""
    async def exit_resource(error_type, primary, traceback):
        try:
            await await_settled(close(error_type, primary, traceback))
        except BaseException as error:
            if primary is None:
                raise
            logger.error('%s_cleanup_secondary_failed error_type=%s', name, type(error).__name__)
        return False
    stack.push_async_exit(exit_resource)


async def _enter_resource(stack, name, manager):
    resource = await manager.__aenter__()
    _register_cleanup(stack, name, manager.__aexit__)
    return resource


class ScopeTasks:
    def __init__(self, owned=None):
        self._locks, self._opening = {}, {}
        self._waiters = {}
        self._owned = owned
        self.closing = False

    @asynccontextmanager
    async def serial(self, key):
        entry = self._locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                del self._locks[key]

    async def open(self, key, factory):
        if self.closing:
            raise RuntimeError('Cannot open a scope while its owner is closing')
        task = self._opening.get(key)
        while task is not None and task.cancelling():
            # A fresh request must not inherit the last waiter's cancellation.
            # Keep the old task registered until its resources have settled.
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
            if self.closing:
                raise RuntimeError('Cannot open a scope while its owner is closing')
            task = self._opening.get(key)
        if task is None:
            async def build():
                async with self.serial(key):
                    return await factory()
            task = asyncio.create_task(build())
            self._opening[key] = task
            if self._owned is not None:
                self._owned.add(task)
            task.add_done_callback(lambda completed: self._retire(key, completed))
        self._waiters[task] = self._waiters.get(task, 0) + 1
        abandoned = False
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            abandoned = bool(asyncio.current_task().cancelling())
            raise
        finally:
            self._waiters[task] -= 1
            if not self._waiters[task]:
                del self._waiters[task]
                if abandoned and not task.done():
                    task.cancel()
                    await await_settled(asyncio.gather(task, return_exceptions=True))

    def _retire(self, key, task):
        if self._opening.get(key) is task:
            del self._opening[key]
        if self._owned is not None:
            self._owned.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error('scope_open_failed error_type=%s', type(error).__name__)

    async def cancel(self, key):
        task = self._opening.get(key)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self):
        self.closing = True
        tasks = tuple(self._opening.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
