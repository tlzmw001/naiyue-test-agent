"""One lazy personal database per application, borrowed by project memory access."""
from pathlib import Path
from threading import RLock

from naiyue_test_agent.storage.database import Database
from naiyue_test_agent.storage.memory import MemoryRepo
from naiyue_test_agent.contracts.memory import MemoryReadError


class PersonalMemory:
    def __init__(self, data_dir, settings):
        self.path = Path(data_dir) / 'personal/memory.sqlite'
        self.settings = settings
        self.lock = RLock()
        self.database = None
        self.closed = False

    def repository(self):
        with self.lock:
            if self.closed:
                raise MemoryReadError('memory_unavailable')
            if self.database is None:
                self.database = Database.open_personal(self.path, self.settings)
            return MemoryRepo(self.database)

    def close(self):
        with self.lock:
            self.closed = True
            if self.database is not None:
                self.database.close()
