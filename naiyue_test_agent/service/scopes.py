"""Bound per-project delivery, sharing only the application's model catalog."""
import asyncio
import logging
from uuid import UUID

from naiyue_test_agent.contracts.projects import LocalSource, ProjectError
from naiyue_test_agent.agent_runtime.async_utils import await_settled
from naiyue_test_agent.application.lifetime import ScopeTasks
from naiyue_test_agent.web.errors import WebError
from naiyue_test_agent.web.run_delivery import RunDelivery
from naiyue_test_agent.web.service import WebService

logger = logging.getLogger(__name__)


class ScopeServices:
    def __init__(self, application, settings, *, settings_path):
        self.application, self.settings = application, settings
        self.settings_path = settings_path
        self.services = {}
        self.output_plans = {}
        self._scopes = ScopeTasks()
        self.closing = False
        self._shutdown_task = None
        self.temporary = None

    async def get(self, scope_id):
        if self.closing:
            raise WebError('service_closing', 503)
        if scope_id.startswith('temp-'):
            self.temporary.require(scope_id, active=True)
        if scope_id in self.services:
            return self.services[scope_id]
        return await self._scopes.open(scope_id, lambda: self._open(scope_id))

    async def _open(self, scope_id):
        if self.closing:
            raise WebError('service_closing', 503)
        if scope_id.startswith('temp-'):
            self.temporary.require(scope_id, active=True)
        if scope_id in self.services:
            return self.services[scope_id]
        if scope_id.startswith('temp-'):
            ticket = self.temporary.require(scope_id, active=True)
            host = await self.application.temporary.open(scope_id)
            values = dict(workspace_root=ticket.root, workspace_id=None)
        elif scope_id == 'chats':
            host = await self.application.chats.open()
            values = dict(workspace_root=None, workspace_id=None)
        else:
            try:
                if str(UUID(scope_id)) != scope_id:
                    raise ValueError('invalid_id')
            except ValueError:
                raise ProjectError('project_not_found') from None
            project = await self.application.projects.get(scope_id)
            host = await self.application.projects.history_host(scope_id)
            values = dict(workspace_root=getattr(project.source,'path',None), workspace_id=project.project_id,
                          database_path=project.database_path)
        settings = self.settings.model_copy(update=values)
        if self.closing:
            raise WebError('service_closing', 503)
        if scope_id.startswith('temp-'):
            self.temporary.require(scope_id, active=True)
        delivery = RunDelivery(host, settings, progress_settings=settings.runtime_settings.progress)
        service = WebService(host, delivery, settings, models=self.application.models,
                             settings_path=self.settings_path)
        self.services[scope_id] = service
        return service

    async def close_project(self, project_id):
        async with self._scopes.serial(project_id):
            await self.application.projects.close(project_id)
            service = self.services.pop(project_id, None)
            if service is not None:
                await service.delivery.close(close_application=False)

    async def cancel_open(self, scope_id):
        await self._scopes.cancel(scope_id)

    async def drain(self):
        results = await asyncio.gather(*(service.delivery.close(close_application=False)
            for service in self.services.values()), return_exceptions=True)
        first = None
        for result in results:
            if isinstance(result, BaseException):
                if first is None:
                    first = result
                else:
                    logger.error('service_delivery_cleanup_secondary_failed error_type=%s', type(result).__name__)
        if first is not None:
            raise first

    async def shutdown(self, receipts):
        if self._shutdown_task is None:
            self.closing = receipts.closing = True
            self._shutdown_task = asyncio.create_task(self._shutdown(receipts))
        await await_settled(self._shutdown_task)

    async def _shutdown(self, receipts):
        first = None
        async def seal_deliveries():
            for service in tuple(self.services.values()):
                await service.delivery.seal()
        cleanups = ([self.temporary.shutdown] if self.temporary is not None else [])
        for cleanup in (self._scopes.shutdown, *cleanups, seal_deliveries, self.application.aclose, self.drain, receipts.drain):
            try:
                await await_settled(cleanup())
            except BaseException as error:
                if first is None:
                    first = error
                else:
                    logger.error('service_cleanup_secondary_failed error_type=%s', type(error).__name__)
        if first is not None:
            raise first
