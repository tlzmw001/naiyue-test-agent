"""Thread-safe terminal bridge; all Agent execution belongs to one async loop."""

import asyncio
import inspect
import logging
from contextlib import AsyncExitStack
from threading import Event, Thread

from .async_application import build_async_local_application
from .async_persistent import open_async_persistent_application


logger = logging.getLogger(__name__)


class AsyncCliApplication:
    def __init__(self, config_path, *, tools, system_prompt, response_format, database_path=None):
        self._ready = Event()
        self._closed = False
        self._thread = Thread(target=self._serve, name='naiyue-cli-async')
        self._thread.start()
        self._ready.wait()
        try:
            self._submit(lambda: self._open(config_path, database_path, tools, system_prompt, response_format))
        except BaseException:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
            raise
        self.default_model_id = self._app.default_model_id

    def _serve(self):
        with asyncio.Runner() as runner:
            self._loop = runner.get_loop()
            self._ready.set()
            self._loop.run_forever()

    async def _open(self, config, database, tools, prompt, response_format):
        self._stack = AsyncExitStack()
        if database is not None:
            self._app = await self._stack.enter_async_context(open_async_persistent_application(
                config, database_path=database, tools=tools, system_prompt=prompt, response_format=response_format))
        else:
            self._app = await build_async_local_application(
                config, tools=tools, system_prompt=prompt, response_format=response_format)
            self._stack.push_async_callback(self._app.aclose)

    def _submit(self, call):
        if self._closed:
            raise RuntimeError('CLI application is closed')

        async def execute():
            result = call()
            return await result if inspect.isawaitable(result) else result

        return asyncio.run_coroutine_threadsafe(execute(), self._loop).result()

    def create_session(self, model_id):
        return self._submit(lambda: self._app.acreate_session(model_id))

    def create_workspace_session(self, model_id, root):
        return self._submit(lambda: self._app.acreate_workspace_session(model_id, root))

    def restore_session(self, session_id):
        return self._submit(lambda: self._app.arestore_session(session_id))

    def select_model(self, session_id, model_id):
        return self._submit(lambda: self._app.aselect_model(session_id, model_id))

    def subscribe_events(self, handler):
        return self._submit(lambda: self._app.subscribe_events(handler))

    def pending_tool_approvals(self, session_id):
        return self._submit(lambda: self._app.pending_tool_approvals(session_id))

    def steer(self, session_id, text):
        return self._submit(lambda: self._app.steer(session_id, text))

    def follow_up(self, session_id, text):
        return self._submit(lambda: self._app.follow_up(session_id, text))

    def request_stop(self, session_id, *, run_id=None):
        return self._submit(lambda: self._app.request_stop(session_id, run_id=run_id))

    def run(self, session_id, messages):
        return self._submit(lambda: self._app.arun(session_id, messages))

    def resume_tool_approval(self, session_id, decisions):
        return self._submit(lambda: self._app.aresume_tool_approval(session_id, decisions))

    def close(self):
        if self._closed:
            return
        try:
            self._submit(self._stack.aclose)
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()


def build_local_application(config_path, *, tools, system_prompt, response_format):
    return AsyncCliApplication(config_path, tools=tools, system_prompt=system_prompt,
                               response_format=response_format)


def build_persistent_application(config_path, *, storage, tools, system_prompt, response_format):
    path = storage.path
    storage.close()  # Release the preflight lock; async ownership rechecks restoration.
    return AsyncCliApplication(config_path, database_path=path, tools=tools,
                               system_prompt=system_prompt, response_format=response_format)
