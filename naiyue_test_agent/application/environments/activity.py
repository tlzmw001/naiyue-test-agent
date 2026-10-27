"""Short binding leases protect live execution without holding a lock over IO."""
from contextlib import contextmanager
import logging
import sqlite3
from threading import RLock
import time

from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.projects import ProjectError
from naiyue_test_agent.storage.errors import SessionPersistenceError

logger=logging.getLogger(__name__)


class EnvironmentActivity:
    def __init__(self,records):
        self._records=records
        self._lock=RLock()
        self._users={};self._managed=set();self._uncertain=set()

    def busy(self,binding):
        with self._lock:return bool(self._users.get(binding,0) or binding in self._managed)

    def uncertain(self,binding):
        with self._lock:return binding in self._uncertain

    def touch(self,binding):
        try:self._records().touch(binding,time.time())
        except (OSError,sqlite3.Error,SessionPersistenceError,SandboxError,ProjectError) as error:
            logger.error('sandbox_activity_save_failed error_type=%s',type(error).__name__)
            with self._lock:self._uncertain.add(binding)

    @contextmanager
    def use(self,binding,*,used=True):
        with self._lock:
            if binding in self._managed:raise SandboxError('env_busy')
            self._users[binding]=self._users.get(binding,0)+1
        try:yield
        finally:
            try:
                if used:self.touch(binding)
            finally:
                with self._lock:
                    self._users[binding]-=1
                    if not self._users[binding]:del self._users[binding]

    @contextmanager
    def manage(self,binding):
        with self._lock:
            if self._users.get(binding,0) or binding in self._managed:raise SandboxError('env_busy')
            self._managed.add(binding)
        try:yield
        finally:
            with self._lock:self._managed.remove(binding)
