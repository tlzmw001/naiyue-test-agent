"""Short binding leases protect live execution without holding a lock over IO."""
from contextlib import contextmanager,asynccontextmanager
from itertools import count
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
        self._touch_sequence=count();self._touches={}

    def busy(self,binding):
        with self._lock:return bool(self._users.get(binding,0) or binding in self._managed)

    def uncertain(self,binding):
        with self._lock:return binding in self._uncertain

    def touch(self,binding):
        with self._lock:
            version=next(self._touch_sequence);self._touches[binding]=version
        saved=False
        try:saved=self._records().touch(binding,time.time())
        except (OSError,sqlite3.Error,SessionPersistenceError,SandboxError,ProjectError) as error:
            logger.error('sandbox_activity_save_failed binding_id=%s error_type=%s',binding,type(error).__name__)
        finally:
            with self._lock:
                if self._touches.get(binding)==version:
                    del self._touches[binding]
                    if saved:self._uncertain.discard(binding)
                    else:self._uncertain.add(binding)

    @asynccontextmanager
    async def ause(self,binding):
        from naiyue_test_agent.agent_runtime.async_utils import run_blocking
        with self.use(binding,used=False):
            try:yield
            finally:await run_blocking(self.touch,binding)

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
    def manage(self,binding,*,interrupt=False):
        with self._lock:
            if (self._users.get(binding,0) and not interrupt) or binding in self._managed:raise SandboxError('env_busy')
            self._managed.add(binding)
        try:yield
        finally:
            with self._lock:self._managed.remove(binding)
