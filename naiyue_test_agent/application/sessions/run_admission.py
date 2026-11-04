"""Serialize root admission and count the actual busy actors across hosts."""
from threading import RLock
from weakref import WeakSet


class RunAdmission:
    def __init__(self, limit):
        self.limit = limit
        self.lock = RLock()
        self.services = WeakSet()

    def release(self,service):
        """Lifetime calls only after operations, runs and process cleanup settle."""
        with self.lock:self.services.discard(service)



    def available(self):
        # Called under the admission lock; each service only holds its own lock
        # while snapshotting. Creation/restoration never takes this outer lock.
        active = 0
        for service in tuple(self.services):
            with service._lock:
                actors = tuple(service._sessions.values())
            active += sum(actor.is_busy() for actor in actors)
        return active < self.limit
