"""One run's cooperative stop signal; never cancels arbitrary tool threads."""


import asyncio
from threading import Event, Lock


class UserStop(BaseException):
    """An owned model operation was cancelled by this run's user stop."""


class RunControl:
    def __init__(self):
        self.signal = Event()
        self._lock = Lock()
        self._model_tasks = set()
        self._reason = None

    @property
    def requested(self):
        return self.signal.is_set()

    @property
    def reason(self):
        return self._reason or 'user'

    def mark_requested(self, reason='user'):
        """Commit the stop fact without calling another event loop."""
        if reason not in ('user', 'failure', 'close'):
            raise ValueError('Invalid trusted stop reason')
        with self._lock:
            if self._reason is None:
                self._reason = reason
            self.signal.set()

    def request(self, reason='user'):
        self.mark_requested(reason)
        with self._lock:
            tasks = tuple(self._model_tasks)
        for task in tasks:
            task.get_loop().call_soon_threadsafe(self._cancel, task)

    @staticmethod
    def _cancel(task):
        if not task.done():
            task.cancel()

    async def await_model(self, call):
        with self._lock:
            if self.requested:
                raise UserStop()
            task = asyncio.create_task(call())
            self._model_tasks.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            # Cancellation of the caller is external and must keep propagating.
            if self.requested and not asyncio.current_task().cancelling():
                raise UserStop() from None
            raise
        finally:
            with self._lock:
                self._model_tasks.discard(task)


def stop_tool_message(call, *, reason='user'):
    from langchain_core.messages import ToolMessage
    cause = {'user': 'the user stopped this run', 'failure': 'the owning run failed',
             'close': 'the application is closing'}[reason]
    code = {'user': 'user_stop', 'failure': 'run_failure', 'close': 'application_close'}[reason]
    return ToolMessage(content=f'Tool was not executed because {cause}. '
                       'No operation was started for this call.',
                       tool_call_id=call['id'], name=call['name'], status='error',
                       additional_kwargs={'naiyue_not_executed': code})


def native_async_model(model):
    from langchain_core.language_models import BaseChatModel
    return (type(model)._agenerate is not BaseChatModel._agenerate
            or type(model)._astream is not BaseChatModel._astream)
