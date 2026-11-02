"""CT1 only serializes registered task admission, revocation and event commits."""
from contextlib import contextmanager
from threading import Lock


class TaskGates:
    def __init__(self, registry):
        self.registry = registry
        self._guard, self._entries = Lock(), {}

    @contextmanager
    def hold(self, task_id):
        self.registry.get_task(task_id)  # Unknown caller IDs cannot allocate gates.
        with self._guard:
            entry = self._entries.setdefault(task_id, [Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1
                if not entry[1]:
                    del self._entries[task_id]
