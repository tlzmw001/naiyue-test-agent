"""Owner-loop idle checks and post-drain stopping; no automatic data deletion."""
import asyncio
from contextvars import Context
import logging
from naiyue_test_agent.diagnostics import safe_log, diagnostic_text
import time

from naiyue_test_agent.agent_runtime.async_utils import run_blocking,await_settled
from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.contracts.projects import ProjectError

logger=logging.getLogger(__name__)


class EnvironmentLifecycle:
    def __init__(self,api):
        self.api=api
        self._wake=asyncio.Event();self._closing=False
        self._task=asyncio.create_task(self._watch(),context=Context())

    @property
    def service(self):return self.api._service

    def wake(self):self._wake.set()

    def rows(self):
        if not (self.service.projects.data_dir/'projects.sqlite').exists():return ()
        return self.service.records().list_all()

    async def _watch(self):
        while not self._closing:
            try:await asyncio.wait_for(self._wake.wait(),self.service.configuration.lifecycle.sweep_seconds)
            except TimeoutError:pass
            self._wake.clear()
            if self._closing:break
            try:await self.api.sweep_idle()
            except Exception as error:safe_log(logger.error, 'sandbox_idle_sweep_failed error_type=%s diagnostic=%s',type(error).__name__,
                diagnostic_text(error=error,phase='idle_sweep'))

    def expired(self,row,now):
        seconds=self.service.configuration.lifecycle.idle_stop_seconds
        return (seconds>0 and row.lifecycle=='running' and row.last_used_at is not None
            and now-row.last_used_at>=seconds and not self.service.activity.uncertain(row.binding_id))

    async def sweep(self,now=None):
        now=time.time() if now is None else now
        rows=await run_blocking(self.rows)
        for row in rows:
            if not self.expired(row,now):continue
            if await run_blocking(self.api._operations.unsettled,row.binding_id):continue
            try:
                with self.service.activity.manage(row.binding_id):
                    if await run_blocking(self.api._operations.unsettled,row.binding_id):continue
                    sessions=await self.api._hosts.list_sessions(row.project_id)
                    session=next((s for s in sessions if s['session_id']==row.session_id),None)
                    if session is None or session['state'] in ('running','interrupted','review_required'):continue
                    def stop_if_idle():
                        current=self.service.get(row.binding_id)
                        if self.expired(current,now) and not self.api._operations.unsettled(row.binding_id):
                            self.service._perform_action(row.binding_id,'stop')
                    await run_blocking(stop_if_idle)
            except SandboxError as error:
                if error.code!='env_busy':safe_log(logger.warning, 'sandbox_idle_stop_unconfirmed code=%s diagnostic=%s',error.code,
                    diagnostic_text(error=error,binding_id=row.binding_id,session_id=row.session_id,phase='idle_stop'))
            except ProjectError as error:
                safe_log(logger.warning, 'sandbox_idle_project_unavailable code=%s diagnostic=%s',error.code,
                    diagnostic_text(error=error,binding_id=row.binding_id,session_id=row.session_id,phase='idle_project'))

    async def aclose(self):
        self._closing=True;self._wake.set()
        await await_settled(self._task)
        await self.api._operations.aclose()

    async def finish_close(self):
        def stop_all():
            first=None
            for row in self.rows():
                if row.lifecycle=='destroyed':continue
                try:
                    with self.service.activity.manage(row.binding_id):
                        self.service._perform_action(row.binding_id,'stop',shutdown=True)
                except Exception as error:
                    safe_log(logger.error, 'sandbox_shutdown_container_unconfirmed error_type=%s diagnostic=%s',type(error).__name__,
                        diagnostic_text(error=error,binding_id=row.binding_id,session_id=row.session_id,phase='shutdown_stop'))
                    if first is None:first=error
            if first is not None:raise first
        await run_blocking(stop_all)
