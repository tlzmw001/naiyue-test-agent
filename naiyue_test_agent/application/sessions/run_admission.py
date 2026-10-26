"""Serialize root admission and count the actual busy actors across hosts."""
from threading import RLock
from weakref import WeakSet
from contextlib import contextmanager,ExitStack
from langchain_core.tools import ToolException
from naiyue_test_agent.application.errors import SessionBusyError


class RunAdmission:
    def __init__(self, limit):
        self.limit = limit
        self.lock = RLock()
        self.services = WeakSet()
        self._delivery_projects=set()

    def release(self,service):
        """Lifetime calls only after operations, runs and process cleanup settle."""
        with self.lock:self.services.discard(service)

    def check_project(self,project_id):
        with self.lock:
            if project_id in self._delivery_projects:raise SessionBusyError('Project delivery is active')

    @contextmanager
    def manage_project(self,project_id):
        with ExitStack() as guards:
            with self.lock:
                self.check_project(project_id)
                for service in tuple(self.services):
                    if service._project is None or service._project.project_id!=project_id:continue
                    with service._lock:
                        if service._closing or service._environment_management or any(actor.is_busy() for actor in service._sessions.values()):
                            raise SessionBusyError('Project has active execution')
                        try:
                            for sid in service._sessions:guards.enter_context(service.background_tasks.deletion_guard(sid))
                        except ToolException as error:raise SessionBusyError('Project has unsettled background tasks') from error
                self._delivery_projects.add(project_id)
            try:yield
            finally:
                with self.lock:
                    guards.close();self._delivery_projects.remove(project_id)

    def available(self):
        # Called under the admission lock; each service only holds its own lock
        # while snapshotting. Creation/restoration never takes this outer lock.
        active = 0
        for service in tuple(self.services):
            with service._lock:
                actors = tuple(service._sessions.values())
            active += sum(actor.is_busy() for actor in actors)
        return active < self.limit
