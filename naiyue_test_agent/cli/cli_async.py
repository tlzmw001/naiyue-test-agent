"""Thread-safe terminal bridge; all Agent execution belongs to one async loop."""

import asyncio
import inspect
import logging
from contextlib import AsyncExitStack
from threading import Event, Thread, current_thread
from typing import Awaitable, Callable, TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from naiyue_test_agent.application.core import Application

T = TypeVar('T')

from naiyue_test_agent.application.async_application import build_async_local_application
from naiyue_test_agent.application.async_persistent import open_async_persistent_application


logger = logging.getLogger(__name__)


class AsyncCliApplication:
    def __init__(self, config_path, *, tools, system_prompt, response_format, database_path=None, initial_active_tools=None, settings=None, workspace_binding=None, expected_project=None):
        self._ready = Event()
        self._closed = False
        self._thread = Thread(target=self._serve, name='naiyue-cli-async')
        self._thread.start()
        self._ready.wait()
        try:
            self._submit(lambda: self._open(config_path, database_path, tools, system_prompt, response_format, initial_active_tools, settings, workspace_binding, expected_project))
        except BaseException:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            raise

    def _serve(self):
        with asyncio.Runner() as runner:
            self._loop = runner.get_loop()
            self._ready.set()
            self._loop.run_forever()

    async def _open(self, config, database, tools, prompt, response_format, initial_active_tools, settings, workspace_binding, expected_project):
        self._stack = AsyncExitStack()
        if database is not None:
            self._app = await self._stack.enter_async_context(open_async_persistent_application(
                config, database_path=database, tools=tools, system_prompt=prompt, response_format=response_format, initial_active_tools=initial_active_tools, settings=settings, workspace_binding=workspace_binding, expected_project=expected_project))
        else:
            self._app = await build_async_local_application(
                config, tools=tools, system_prompt=prompt, response_format=response_format, initial_active_tools=initial_active_tools, settings=settings)
            self._stack.push_async_callback(self._app.aclose)

    def call(self, operation: Callable[["Application"], T | Awaitable[T]]) -> T:
        return self._submit(lambda: operation(self._app))

    def _submit(self, call):
        if current_thread() is self._thread:
            raise RuntimeError('CLI dispatch cannot wait on its owning event loop')
        if self._closed:
            raise RuntimeError('CLI application is closed')

        async def execute():
            result = call()
            return await result if inspect.isawaitable(result) else result

        return asyncio.run_coroutine_threadsafe(execute(), self._loop).result()

    def close(self):
        if self._closed:
            return
        try:
            self._submit(self._stack.aclose)
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()


def build_local_application(config_path, *, tools, system_prompt, response_format, initial_active_tools=None, settings=None):
    return AsyncCliApplication(config_path, tools=tools, system_prompt=system_prompt,
                               response_format=response_format, initial_active_tools=initial_active_tools, settings=settings)


def build_persistent_application(config_path, *, storage, tools, system_prompt, response_format, initial_active_tools=None, settings=None, expected_project=None):
    path = storage.path
    binding = storage.sessions.get_workspace_binding() if expected_project is None else None
    storage.close()  # Release the preflight lock; async ownership rechecks restoration.
    return AsyncCliApplication(config_path, database_path=path, workspace_binding=binding, expected_project=expected_project, tools=tools,
                               system_prompt=system_prompt, response_format=response_format, initial_active_tools=initial_active_tools, settings=settings)
