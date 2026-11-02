"""Deletion admission and in-flight file IO; counts are never locks across IO."""
from contextlib import contextmanager, asynccontextmanager
from threading import Condition
from naiyue_test_agent.contracts.cloud_tasks import CloudError


class TaskIO:
    def __init__(self, runtime):
        self.runtime = runtime
        self.condition = Condition()
        self.active = {}

    @contextmanager
    def hold(self, task_id):
        self._enter(task_id)
        try: yield
        finally: self._leave(task_id)

    def _enter(self, task_id):
        with self.runtime.gates.hold(task_id):
            self.runtime.available_task(task_id)
            with self.condition:
                self.active[task_id] = self.active.get(task_id, 0) + 1
    def _leave(self, task_id):
        with self.condition:
            self.active[task_id] -= 1
            if not self.active[task_id]: del self.active[task_id]
            self.condition.notify_all()

    @asynccontextmanager
    async def use(self, task_id):
        from .work import cloud_work
        entered = False
        def enter():
            nonlocal entered
            self._enter(task_id); entered = True
        try:
            await cloud_work(self.runtime, enter, drain_cancel=True)
            yield
        finally:
            if entered: self._leave(task_id)

    def drain(self, task_id):
        with self.condition:
            if not self.condition.wait_for(lambda: task_id not in self.active,
                    self.runtime.settings.delete_drain_timeout_seconds):
                raise CloudError('task_delete_io_pending', 503)
