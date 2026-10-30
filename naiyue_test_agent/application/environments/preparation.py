"""Service-owned, bounded preparation jobs; worker checkpoints never hold IO locks."""
import asyncio
from contextvars import Context
import logging
from threading import Event, RLock

from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.diagnostics import safe_log

logger=logging.getLogger(__name__)


class PreparationProgress:
    def __init__(self,binding,project,session):
        self.binding,self.project,self.session=binding,project,session
        self.cancelled=Event();self.lock=RLock()
        self.state='preparing';self.phase='queued';self.completed=None;self.total=None;self.error=None

    def check(self):
        if self.cancelled.is_set():raise SandboxError('env_preparation_cancelled')

    def update(self,phase,*,completed=None,total=None):
        with self.lock:
            self.check()
            self.phase,self.completed,self.total=phase,completed,total

    def stop(self):
        with self.lock:
            self.cancelled.set()
            if self.state=='preparing':self.phase='stopping';self.cancelled.set()

    def view(self):
        with self.lock:
            return dict(state=self.state,phase=self.phase,completed=self.completed,total=self.total,error_code=self.error)


class SandboxPreparations:
    def __init__(self,limit):
        self._limit=limit;self._jobs={};self._guard=RLock();self._closing=False

    def start(self,binding,project,session,prepare):
        with self._guard:
            if self._closing:raise SandboxError('env_stopped')
            previous=self._jobs.get(binding)
            if previous is not None and not previous[1].done():return previous[1]
            if previous is None:
                for key,(progress,task) in list(self._jobs.items()):
                    if len(self._jobs)<self._limit():break
                    if task.done():del self._jobs[key]
                if len(self._jobs)>=self._limit():raise SandboxError('env_retained_capacity')
            progress=PreparationProgress(binding,project,session)
            task=asyncio.create_task(self._run(progress,prepare),context=Context())
            self._jobs[binding]=(progress,task)
            # The service owns completion even when the initiating HTTP request leaves.
            task.add_done_callback(lambda finished:None if finished.cancelled() else finished.exception())
            return task

    async def _run(self,progress,prepare):
        try:
            result=await prepare(progress)
            progress.check()
        except BaseException as error:
            cancelled=progress.cancelled.is_set() or isinstance(error,asyncio.CancelledError)
            with progress.lock:
                progress.state='cancelled' if cancelled else 'failed'
                progress.error='env_preparation_cancelled' if cancelled else getattr(error,'code','env_operation_unknown')
            safe_log(logger.warning,'sandbox_preparation_failed binding_id=%s phase=%s error_type=%s',
                progress.binding,progress.phase,type(error).__name__)
            raise
        with progress.lock:progress.state='ready';progress.phase='ready'
        return result

    def view(self,binding):
        with self._guard:item=self._jobs.get(binding)
        return item[0].view() if item is not None else None

    async def wait(self,binding):
        with self._guard:item=self._jobs.get(binding)
        if item is None:raise SandboxError('env_preparation_unavailable')
        await asyncio.shield(item[1])
        item[0].check()
        return item[0]

    def active(self):
        with self._guard:
            return [dict(binding_id=p.binding,project_id=p.project,session_id=p.session,preparation=p.view())
                for p,t in self._jobs.values() if not t.done()]

    async def stop(self,binding):
        with self._guard:item=self._jobs.get(binding)
        if item is not None:
            item[0].stop()
            await await_settled(asyncio.gather(item[1],return_exceptions=True))

    async def close(self):
        with self._guard:
            self._closing=True
            items=list(self._jobs.values())
            for progress,_ in items:progress.stop()
        await await_settled(asyncio.gather(*(task for _,task in items),return_exceptions=True))
